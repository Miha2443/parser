"""Полный прогон по всем источникам недвижимости с автоархивированием.

Запуск:
    py scripts/update_realty.py              # всё
    py scripts/update_realty.py monitoring   # один источник
    py scripts/update_realty.py --no-archive # без архивирования старого
    py scripts/update_realty.py --skip-kvart-per-dev  # без долгого per-dev обхода
    py scripts/update_realty.py --weekly-kvart-per-dev  # per-dev только по понедельникам

Порядок выполнения:
  1. nashdom_checker monitoring_2_0   (~1 мин, requests)
  2. nashdom_checker rasprodannost    (инкрементально; полный обход ~15-30 мин)
  3. nashdom_checker kvartirografia   (~5 мин агрегаты + ~90 мин per-dev обход)
  4. erzrf_checker top                (~5 мин)
  5. erzrf_checker cards              (~10 мин, по топ-100)
  → ПОСЛЕ всего: архивируется устаревшее в data/raw/realty/_archive/<date>/
  → Уведомление в TDM с детальным отчётом что обновилось

Escrow (data/raw/realty/escrow_manual/) — РУЧНАЯ выгрузка с
ДОМ.РФ ЕИСЖС, парсера нет. Положи свежий xlsx туда сам.

Источники для аргумента (можно несколько через пробел):
  monitoring     → nashdom monitoring_2_0
  rasprod        → nashdom rasprodannost
  kvart          → nashdom kvartirografia
  erz-top        → erzrf top
  erz-cards      → erzrf cards
  nashdom        → все nashdom-источники
  erzrf          → все erzrf-источники
"""
from __future__ import annotations

import argparse
import atexit
import hashlib
import json
import os
import subprocess
import sys
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from pipeline.state_utils import load_json_state

REALTY_ROOT = ROOT / "data" / "raw" / "realty"
LOG_DIR = ROOT / "logs"
PROCESSED_DIR = ROOT / "data" / "processed"
REALTY_STATUS_FILE = PROCESSED_DIR / "realty_update_status.json"
REALTY_MARTS_MANIFEST = ROOT / "data" / "marts" / "realty" / "manifest.json"
REALTY_UPDATE_LOCK = ROOT / "state" / ".realty_update.lock"
REALTY_UPDATE_LOCK_STALE_SEC = 12 * 3600

# Глобальный файл лога текущего прогона. Инициализируется в main().
_LOG_FILE: Path | None = None
_LOG_FH = None
_ACTIVE_REALTY_RUN = None
_REALTY_UPDATE_LOCK_HELD = False
_REALTY_UPDATE_LOCK_TOKEN: str | None = None

# Карта алиасов: алиас → (скрипт, аргументы)
SOURCE_MAP = {
    "monitoring": ("nashdom_checker.py", ["monitoring_2_0"]),
    "rasprod":    ("nashdom_checker.py", ["rasprodannost"]),
    "kvart":      ("nashdom_checker.py", ["kvartirografia"]),
    "erz-top":    ("erzrf_checker.py",   ["top"]),
    "erz-cards":  ("erzrf_checker.py",   ["cards"]),
    "fedstat":    ("fedstat_checker.py", []),   # зарплата, ИПЦ, ВРП и пр.
    "rosstat":    ("rosstat_checker.py", []),   # ВРП/ВВП по годам
}

GROUP_MAP = {
    "nashdom": ["monitoring", "rasprod", "kvart"],
    "erzrf":   ["erz-top", "erz-cards"],
    "stats":   ["fedstat", "rosstat"],
    "all":     ["monitoring", "rasprod", "kvart", "erz-top", "erz-cards",
                "fedstat", "rosstat"],
}

# Watchdog-таймауты per-source (минуты). Если subprocess не завершился —
# убиваем дерево процессов (включая Chrome) и помечаем как failure.
SOURCE_TIMEOUT_MIN = {
    "monitoring":  5,
    "rasprod":     180,
    "kvart":       180,
    "erz-top":     15,
    "erz-cards":   30,
    "fedstat":     30,
    "rosstat":     30,
}
DEFAULT_TIMEOUT_MIN = 60

# Для архивации после каждого источника: какие префиксы файлов и в
# каких папках принадлежат этому источнику.
SOURCE_ARCHIVE_PATHS = {
    "monitoring":  ["nashdom"],
    "rasprod":     ["nashdom"],
    "kvart":       ["nashdom"],
    "erz-top":     ["erzrf"],
    "erz-cards":   ["erzrf/cards", "erzrf"],
}
SOURCE_PREFIXES = {
    "monitoring":  ["monitoring_2_0_"],
    "rasprod":     ["rasprodannost_"],
    "kvart":       ["kvartirografia_"],
    "erz-top":     ["top_obyem_", "top_developers_", "top_nakopl_",
                    "top_skorost_", "top_potreb_"],
    "erz-cards":   ["cards_", "card_"],
}

SOURCE_MARTS = {
    "monitoring": {"monitoring_2_0"},
    "rasprod": {"rasprodannost"},
    "kvart": {"kvartirografia"},
    "erz-top": {"erzrf_top"},
    "erz-cards": {"erzrf_cards"},
    "escrow-manual": {"escrow_manual"},
    "rosstat": {"vvod_static", "emiss_34118"},
}

# Параллельный пул для волн (можно урезать через env PARALLEL_LIMIT=2).
PARALLEL_LIMIT = max(1, int(os.environ.get("PARALLEL_LIMIT", "4")))

# Волны: внутри волны источники запускаются параллельно, между волнами —
# последовательно (erz-cards зависит от top_developers_*.json от erz-top).
# rasprod вынесен в свою волну: держит в памяти tables 77 периодов × ~2500
# строк, при параллельной нагрузке (4 Chrome'а) на 30-м периоде падает с
# MemoryError. Соло — RAM хватает.
# fedstat — отдельной волной по исторической причине (раньше я думал что
# параллельность ломает; сейчас исправлено downgrade'ом selenium до 4.43,
# но оставляю в отдельной волне как буфер).
WAVES_DEFAULT = [
    ["monitoring", "kvart", "erz-top", "rosstat"],
    ["erz-cards"],
    ["rasprod"],
    ["fedstat"],
]


# Lock для упорядоченной печати из параллельных потоков.
_print_lock = threading.Lock()


def _print(msg: str = "") -> None:
    with _print_lock:
        print(msg, flush=True)
        if _LOG_FH is not None:
            try:
                _LOG_FH.write(msg + "\n")
                _LOG_FH.flush()
            except Exception:  # noqa: BLE001
                pass


def _setup_logging() -> Path:
    """Открывает logs/update_<timestamp>.log на запись; ротирует старые.

    Хранит последние 20 логов прогонов, остальное удаляет — чтоб папка
    не разрасталась. Возвращает путь к свежему файлу лога.
    """
    global _LOG_FILE, _LOG_FH
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    _LOG_FILE = LOG_DIR / f"update_{ts}.log"
    _LOG_FH = open(_LOG_FILE, "w", encoding="utf-8", buffering=1)
    # Ротация — удаляем всё старше 20-го прогона.
    old_logs = sorted(LOG_DIR.glob("update_*.log"))
    for path in old_logs[:-20]:
        try:
            path.unlink()
        except OSError:
            pass
    return _LOG_FILE


def _close_logging() -> None:
    global _LOG_FH
    if _LOG_FH is not None:
        try:
            _LOG_FH.close()
        except Exception:  # noqa: BLE001
            pass
        _LOG_FH = None


def acquire_realty_update_lock(
    lock_path: Path = REALTY_UPDATE_LOCK,
    *,
    stale_after_sec: int = REALTY_UPDATE_LOCK_STALE_SEC,
) -> bool:
    """Atomically acquire the top-level realty update lock."""
    global _REALTY_UPDATE_LOCK_HELD, _REALTY_UPDATE_LOCK_TOKEN
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    stale_lock = False
    try:
        age_sec = time.time() - lock_path.stat().st_mtime
        stale_lock = age_sec > stale_after_sec
    except FileNotFoundError:
        pass
    except OSError:
        return False

    if stale_lock:
        try:
            lock_path.unlink()
        except FileNotFoundError:
            pass
        except OSError:
            return False

    try:
        fd = os.open(str(lock_path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError:
        return False
    except OSError:
        return False

    token = uuid.uuid4().hex
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.write(f"token={token}\n")
        fh.write(f"pid={os.getpid()}\n")
        fh.write(f"started_at={datetime.now().isoformat(timespec='seconds')}\n")
    _REALTY_UPDATE_LOCK_HELD = True
    _REALTY_UPDATE_LOCK_TOKEN = token
    return True


def release_realty_update_lock(lock_path: Path = REALTY_UPDATE_LOCK) -> None:
    """Release the top-level realty update lock if this process acquired it."""
    global _REALTY_UPDATE_LOCK_HELD, _REALTY_UPDATE_LOCK_TOKEN
    if not _REALTY_UPDATE_LOCK_HELD:
        return
    try:
        text = lock_path.read_text(encoding="utf-8")
        if _REALTY_UPDATE_LOCK_TOKEN and f"token={_REALTY_UPDATE_LOCK_TOKEN}\n" in text:
            lock_path.unlink(missing_ok=True)
    except FileNotFoundError:
        pass
    except OSError:
        pass
    _REALTY_UPDATE_LOCK_HELD = False
    _REALTY_UPDATE_LOCK_TOKEN = None


def _write_json_atomic(path: Path, payload: dict) -> None:
    tmp = path.with_name(f"{path.name}.tmp")
    try:
        tmp.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        json.loads(tmp.read_text(encoding="utf-8"))
        tmp.replace(path)
    finally:
        try:
            if tmp.exists():
                tmp.unlink()
        except OSError:
            pass


def write_realty_status(payload: dict) -> None:
    """Пишет машинно-читаемый статус последнего realty-прогона для дашборда."""
    try:
        PROCESSED_DIR.mkdir(parents=True, exist_ok=True)
        _write_json_atomic(REALTY_STATUS_FILE, payload)
    except (OSError, json.JSONDecodeError) as exc:
        _print(f"⚠️  Не удалось записать {REALTY_STATUS_FILE}: {exc}")


def write_realty_run_status(
    status: str,
    *,
    started: float,
    sources: list[str],
    log_path: Path | None,
    **fields,
) -> None:
    """Write a normalized update status payload for the dashboard."""
    now = datetime.now()
    payload = {
        "status": status,
        "started_at": datetime.fromtimestamp(started).isoformat(timespec="seconds"),
        "updated_at": now.isoformat(timespec="seconds"),
        "finished_at": None if status == "running" else now.isoformat(timespec="seconds"),
        "duration_sec": round(time.time() - started, 2),
        "sources_requested": sources,
        "successes": fields.pop("successes", []),
        "failures": fields.pop("failures", []),
    }
    if log_path is not None:
        payload["log_file"] = str(log_path.relative_to(ROOT)).replace("\\", "/")
    payload.update(fields)
    write_realty_status(payload)


def set_active_realty_run(**context) -> None:
    """Remember current run context so unexpected crashes do not leave running status."""
    global _ACTIVE_REALTY_RUN
    _ACTIVE_REALTY_RUN = context


def clear_active_realty_run() -> None:
    global _ACTIVE_REALTY_RUN
    _ACTIVE_REALTY_RUN = None


def mark_active_realty_run_failed(exc: BaseException) -> None:
    """Best-effort failed status for unhandled exceptions after a run has started."""
    if not _ACTIVE_REALTY_RUN:
        return
    ctx = dict(_ACTIVE_REALTY_RUN)
    try:
        write_realty_run_status(
            "failed",
            started=ctx["started"],
            sources=ctx["sources"],
            log_path=ctx.get("log_path"),
            successes=list(ctx.get("successes") or []),
            failures=list(ctx.get("failures") or []),
            error=f"{type(exc).__name__}: {exc}",
            archive=ctx.get("archive"),
            keep=ctx.get("keep"),
            force=ctx.get("force"),
            full_rasprod_history=ctx.get("full_rasprod_history"),
            selenium_sleep_scale=ctx.get("selenium_sleep_scale"),
        )
    except Exception:  # noqa: BLE001
        pass


def write_active_realty_run_progress(**fields) -> None:
    """Best-effort progress update for the dashboard while a run is still active."""
    if not _ACTIVE_REALTY_RUN:
        return
    ctx = dict(_ACTIVE_REALTY_RUN)
    successes = list(ctx.get("successes") or [])
    failures = list(ctx.get("failures") or [])
    sources = list(ctx.get("sources") or [])
    completed = list(dict.fromkeys(successes + failures))
    pending = [source for source in sources if source not in completed]
    try:
        write_realty_run_status(
            "running",
            started=ctx["started"],
            sources=sources,
            log_path=ctx.get("log_path"),
            successes=successes,
            failures=failures,
            completed_sources=completed,
            pending_sources=pending,
            archive=ctx.get("archive"),
            keep=ctx.get("keep"),
            force=ctx.get("force"),
            full_rasprod_history=ctx.get("full_rasprod_history"),
            selenium_sleep_scale=ctx.get("selenium_sleep_scale"),
            **fields,
        )
    except Exception:  # noqa: BLE001
        pass


def _kill_process_tree(pid: int) -> None:
    """Принудительно убивает процесс и всех его потомков (включая Chrome)."""
    if sys.platform == "win32":
        subprocess.run(
            ["taskkill", "/F", "/T", "/PID", str(pid)],
            capture_output=True, check=False,
        )
        return
    # POSIX
    try:
        import signal
        try:
            import psutil  # type: ignore
            parent = psutil.Process(pid)
            for child in parent.children(recursive=True):
                try:
                    child.kill()
                except Exception:  # noqa: BLE001
                    pass
            try:
                parent.kill()
            except Exception:  # noqa: BLE001
                pass
        except ImportError:
            os.kill(pid, signal.SIGKILL)
    except Exception:  # noqa: BLE001
        pass


SNAPSHOT_DIRS = [
    (REALTY_ROOT, "realty", ""),
    (ROOT / "downloads", "downloads", ""),
]


def snapshot_files(
    *,
    roots: list[tuple[Path, str, str]] | None = None,
    file_prefixes: list[str] | None = None,
) -> dict[str, tuple[int, str]]:
    """Snapshot файлов realty/ + downloads/: {prefix:rel_path → (size, sha256_head)}.

    Хеш по первым 256 КБ — быстро и достаточно для детекта изменений.
    """
    out: dict[str, tuple[int, str]] = {}
    for base, namespace, rel_prefix in roots or SNAPSHOT_DIRS:
        if not base.exists():
            continue
        for f in base.rglob("*"):
            if not f.is_file() or "_archive" in f.parts:
                continue
            if file_prefixes and not any(f.name.startswith(prefix) for prefix in file_prefixes):
                continue
            try:
                size = f.stat().st_size
                h = hashlib.sha256()
                with f.open("rb") as fh:
                    h.update(fh.read(256 * 1024))
                rel = str(f.relative_to(base)).replace("\\", "/")
                out[f"{namespace}:{rel_prefix}{rel}"] = (size, h.hexdigest()[:16])
            except OSError:
                pass
    return out


def snapshot_scope_for_source(alias: str) -> tuple[list[tuple[Path, str, str]] | None, list[str] | None]:
    """Return a narrow snapshot scope for per-source deduplication."""
    paths = SOURCE_ARCHIVE_PATHS.get(alias)
    prefixes = SOURCE_PREFIXES.get(alias)
    if not paths:
        return None, None
    roots = []
    for rel in paths:
        rel_key = rel.replace("\\", "/")
        roots.append((REALTY_ROOT / rel, "realty", f"{rel_key}/"))
    return roots, prefixes


# Классификатор: путь файла → бизнес-название источника.
# Применяется к именам из snapshot: «prefix:rel_path».
def classify_file(rel_with_prefix: str) -> str | None:
    """Возвращает бизнес-название («ИПЦ», «Квартирография», …) или None."""
    p = rel_with_prefix.lower()
    rules = [
        ("monitoring_2_0", "Мониторинг 2.0"),
        ("kvartirografia", "Квартирография"),
        ("rasprodannost", "Распроданность"),
        ("top_obyem_vvoda", "ERZ ввод"),
        ("top_obyem_stroitelstva", "ERZ строительство"),
        ("top_nakopl_vvod", "ERZ накопл. ввод"),
        ("top_skorost", "ERZ скорость"),
        ("top_potreb_kachestva", "ERZ потреб. качества"),
        ("top_developers", "ERZ список топ-100"),
        ("cards_", "ERZ карточки"),
        ("наполняемость", "Наполняемость счетов (эскроу)"),
        ("эскроу", "Наполняемость счетов (эскроу)"),
        ("escrow", "Наполняемость счетов (эскроу)"),
        ("среднемесячная", "Зарплата"),
        ("заработная плата", "Зарплата"),
        ("потребительских цен", "ИПЦ"),
        ("индексы потребительских", "ИПЦ"),
        ("инвестиции в основной", "Инвестиции"),
        ("введено в действие", "Ввод жилья (Росстат)"),
        ("количество построенных квартир", "Количество построенных квартир"),
        ("численность", "Численность населения"),
        ("валовой региональный", "ВРП"),
        ("валовый региональный", "ВРП"),
        ("врп", "ВРП"),
        ("ввп", "ВВП"),
        ("vrp", "ВРП"),
        ("vvp", "ВВП"),
        ("vds", "ВДС"),
        ("индекс предпринимательской", "Индекс предпр. уверенности"),
        ("трудовых ресурсов", "Трудовые ресурсы"),
        ("занятых в экономике", "Занятые в экономике"),
        ("средняя цена 1 кв", "Средняя цена м²"),
        ("средние потребительские цены", "Средние цены товаров/услуг"),
    ]
    for needle, label in rules:
        if needle in p:
            return label
    return None


def summarize_by_topic(paths: list[str]) -> list[str]:
    """Группирует пути по бизнес-темам. Возвращает уникальные темы (сортированные)."""
    topics: set[str] = set()
    unknown: list[str] = []
    for p in paths:
        label = classify_file(p)
        if label:
            topics.add(label)
        else:
            unknown.append(p)
    out = sorted(topics)
    if unknown:
        out.append(f"прочее ({len(unknown)})")
    return out


def deduplicate_new_files(
    before_snapshot: dict,
    *,
    roots: list[tuple[Path, str, str]] | None = None,
    file_prefixes: list[str] | None = None,
) -> tuple[int, int]:
    """После прогонов парсеров находит появившиеся файлы и проверяет
    каждый на дубль (по контенту) с тем что уже есть в архиве.

    Дубликаты УДАЛЯЮТСЯ — мы не считаем такой прогон «обновлением»,
    хотя файл был скачан.

    Возвращает (deduped, real_new):
      deduped — сколько файлов удалено как дубли
      real_new — сколько файлов реально новых/изменённых
    """
    sys.path.insert(0, str(ROOT))
    from pipeline.deduplicate import deduplicate as _dedupe

    after = snapshot_files(roots=roots, file_prefixes=file_prefixes)
    added_paths = sorted(set(after) - set(before_snapshot))
    if not added_paths:
        return 0, 0
    _print(f"\n{'─'*60}")
    _print(f"🔍 Дедупликация новых файлов ({len(added_paths)} шт)")
    _print(f"{'─'*60}")
    deduped = 0
    real_new = 0
    prefix_to_base = {namespace: base for base, namespace, _ in SNAPSHOT_DIRS}
    for rel in added_paths:
        if ":" in rel:
            prefix, sub = rel.split(":", 1)
            base = prefix_to_base.get(prefix)
            if base is None:
                continue
            f = base / sub
        else:
            f = REALTY_ROOT / rel  # backward compat для старых snapshot
        if not f.exists():
            continue
        kept, is_update = _dedupe(f, log_prefix="  ")
        if is_update:
            real_new += 1
        else:
            deduped += 1
    if deduped:
        _print(f"  Итого: {real_new} новых, {deduped} дублей удалено")
    else:
        _print(f"  Все {real_new} файлов уникальны (дублей нет)")
    return deduped, real_new


def diff_snapshots(before: dict, after: dict) -> dict:
    """Сравнение двух snapshot'ов. Возвращает {added, changed, removed}."""
    added = sorted(set(after) - set(before))
    removed = sorted(set(before) - set(after))
    changed = sorted(p for p in set(before) & set(after)
                     if before[p] != after[p])
    return {"added": added, "changed": changed, "removed": removed}


def run_source(alias: str, env: dict, force: bool = False,
               keep: int = 1, do_archive: bool = True) -> bool:
    """Запускает один источник.

    Запускает subprocess с watchdog'ом (per-source timeout). Stdout
    стримится с префиксом `[alias]` чтобы вывод параллельных источников
    не смешивался. После успеха — дедупликация и архивация СВОЕГО
    семейства (старые файлы → _archive/<date>/<source>/).
    """
    if alias not in SOURCE_MAP:
        _print(f"⚠️  Неизвестный источник: {alias}")
        return False
    script, args = SOURCE_MAP[alias]
    extra = []
    if force and alias in ("fedstat", "rosstat", "monitoring", "rasprod", "kvart"):
        extra.append("--force")
    cmd = [sys.executable, "-u", str(ROOT / script), *args, *extra]
    timeout_min = SOURCE_TIMEOUT_MIN.get(alias, DEFAULT_TIMEOUT_MIN)
    prefix = f"[{alias:>10}]"

    _print(f"\n{'─'*60}")
    _print(f"▶ {alias} (watchdog: {timeout_min}мин)")
    _print(f"{'─'*60}")

    # Snapshot до источника — для дедупликации только его файлов.
    snapshot_roots, snapshot_prefixes = snapshot_scope_for_source(alias)
    before_src = (
        snapshot_files(roots=snapshot_roots, file_prefixes=snapshot_prefixes)
        if do_archive
        else {}
    )

    # Принудительно utf-8 в child: с stdout=PIPE Python берёт кодировку
    # по locale (cp1251 на Windows) — любая эмодзи в print() падает с
    # UnicodeEncodeError. PYTHONIOENCODING переключает sys.stdout/stderr
    # на utf-8, PYTHONUTF8=1 включает utf-8 mode для всего runtime.
    # PYTHONUNBUFFERED=1 + `python -u` отключают stdout-буферизацию child'а
    # — без этого print() из парсеров накапливается блоками по 4-8КБ и
    # вываливается «оптом», и пока тишина — непонятно жив парсер или нет.
    env_utf8 = dict(env)
    env_utf8["PYTHONIOENCODING"] = "utf-8"
    env_utf8["PYTHONUTF8"] = "1"
    env_utf8["PYTHONUNBUFFERED"] = "1"

    started = time.time()
    try:
        proc = subprocess.Popen(
            cmd, cwd=ROOT, env=env_utf8,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, encoding="utf-8", errors="replace",
            bufsize=1,
        )
    except Exception as exc:  # noqa: BLE001
        _print(f"❌ {alias}: не удалось запустить процесс: {exc}")
        return False

    # Stream stdout в отдельном потоке с префиксом alias.
    def _stream() -> None:
        try:
            for line in proc.stdout:  # type: ignore[union-attr]
                _print(f"{prefix} {line.rstrip()}")
        except Exception:  # noqa: BLE001
            pass

    reader = threading.Thread(target=_stream, daemon=True)
    reader.start()

    timed_out = False
    try:
        proc.wait(timeout=timeout_min * 60)
    except subprocess.TimeoutExpired:
        timed_out = True
        _print(f"⏰ {alias}: убит по watchdog'у (>{timeout_min}мин), "
               f"гашу процессы Chrome")
        _kill_process_tree(proc.pid)
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            pass
    except KeyboardInterrupt:
        _print(f"\n⚠️  {alias}: прервано пользователем")
        _kill_process_tree(proc.pid)
        raise

    reader.join(timeout=5)
    elapsed = time.time() - started

    if timed_out:
        return False
    rc = proc.returncode
    if rc == 0:
        _print(f"✅ {alias}: успех (за {elapsed/60:.1f} мин)")
        if do_archive:
            try:
                deduplicate_new_files(
                    before_src,
                    roots=snapshot_roots,
                    file_prefixes=snapshot_prefixes,
                )
            except Exception as exc:  # noqa: BLE001
                _print(f"⚠️  {alias}: дедупликация упала — {exc}")
            if not archive_old_for_source(alias, keep=keep):
                return False
        return True
    _print(f"❌ {alias}: код выхода {rc} (за {elapsed/60:.1f} мин)")
    return False


def archive_old(keep: int = 1) -> bool:
    """Перемещает устаревшие выгрузки в _archive/<date>/ (полная зачистка)."""
    cmd = [sys.executable, "-m", "pipeline.archive_old", "--keep", str(keep)]
    _print(f"\n{'─'*60}")
    _print(f"📦 Финальная архивация (safety-net, keep={keep})")
    _print(f"{'─'*60}")
    result = subprocess.run(cmd, cwd=ROOT, check=False)
    if result.returncode != 0:
        _print(f"⚠️  Финальная архивация завершилась с кодом {result.returncode}")
        return False
    return True


def archive_old_for_source(alias: str, keep: int = 1) -> bool:
    """Архивирует устаревшие файлы только этого источника.

    Принцип: для каждого пути из SOURCE_ARCHIVE_PATHS[alias] зовём
    pipeline.archive_old с --paths и --prefixes — это ограничивает
    архивацию только семействами alias'а (важно для nashdom, где
    monitoring_2_0_*, rasprodannost_* и kvartirografia_* лежат вместе).
    """
    paths = SOURCE_ARCHIVE_PATHS.get(alias)
    prefixes = SOURCE_PREFIXES.get(alias)
    if not paths or not prefixes:
        return True
    paths_arg = [f"realty/{p}" for p in paths]
    cmd = [
        sys.executable, "-m", "pipeline.archive_old",
        "--keep", str(keep),
        "--paths", *paths_arg,
        "--prefixes", *prefixes,
    ]
    result = subprocess.run(cmd, cwd=ROOT, check=False)
    if result.returncode != 0:
        _print(f"⚠️  {alias}: архивация завершилась с кодом {result.returncode}")
        return False
    return True


def check_escrow():
    """Подсказка про эскроу."""
    escrow_dir = REALTY_ROOT / "escrow_manual"
    files = list(escrow_dir.glob("*.xlsx")) if escrow_dir.exists() else []
    _print(f"\n{'─'*60}")
    _print(f"📋 Эскроу (ручная выгрузка)")
    _print(f"{'─'*60}")
    if not files:
        _print(f"⚠️  Папка пустая: {escrow_dir}")
        _print(f"   Скачай «Наполняемость счетов.xlsx» с ДОМ.РФ ЕИСЖС вручную")
    else:
        latest = max(files, key=lambda p: p.stat().st_mtime)
        date = datetime.fromtimestamp(latest.stat().st_mtime).strftime("%d.%m.%Y")
        _print(f"✓ Файл есть: {latest.name} (от {date})")


def select_realty_marts_for_sources(successes: list[str]) -> set[str] | None:
    """Какие realty-витрины нужно пересобрать после успешных источников.

    None означает полный bootstrap-build, например когда manifest ещё нет.
    Пустое множество означает что realty-витрины этим прогоном не затронуты.
    """
    if not has_valid_realty_marts_manifest():
        return None
    marts: set[str] = set()
    for alias in successes:
        marts.update(SOURCE_MARTS.get(alias, set()))
    if marts:
        # Ручной escrow не имеет парсера, но он быстрый и часто обновляется
        # рядом с realty-прогоном. Держим его mart свежим без полной сборки.
        marts.add("escrow_manual")
    return marts


def source_alias_for_changed_path(rel_with_prefix: str) -> str | None:
    """Maps a snapshot path (`realty:...` / `downloads:...`) to update alias."""
    p = rel_with_prefix.lower().replace("\\", "/")
    if "monitoring_2_0" in p:
        return "monitoring"
    if "rasprodannost" in p:
        return "rasprod"
    if "kvartirografia" in p:
        return "kvart"
    if "realty:erzrf/cards/" in p or "/cards_" in p or "/card_" in p:
        return "erz-cards"
    if any(prefix in p for prefix in (
        "top_obyem_",
        "top_developers_",
        "top_nakopl_",
        "top_skorost_",
        "top_potreb_",
    )):
        return "erz-top"
    if (
        "realty:vvod/" in p
        or "emiss_34118" in p
        or "stroi_111" in p
        or "vvod.xlsx" in p
        or "34118_filter" in p
    ):
        return "rosstat"
    if "emiss_34118" in p or "введено в действие общей площади жилых домов" in p:
        return "rosstat"
    if (
        "realty:escrow_manual/" in p
        or "escrow" in p
        or "эскроу" in p
        or "наполняемость" in p
    ):
        return "escrow-manual"
    return None


def select_realty_marts_for_changes(changed_paths: list[str]) -> set[str] | None:
    """Select marts affected by actual changed/added files after dedupe."""
    if not has_valid_realty_marts_manifest():
        return None
    aliases = sorted({
        alias for path in changed_paths
        if (alias := source_alias_for_changed_path(path)) is not None
    })
    selected = select_realty_marts_for_sources(aliases)
    if selected is None:
        return None
    selected.update(select_repair_realty_marts())
    return selected


def has_valid_realty_marts_manifest() -> bool:
    if not REALTY_MARTS_MANIFEST.exists():
        return False
    try:
        manifest = json.loads(REALTY_MARTS_MANIFEST.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    marts = manifest.get("marts") if isinstance(manifest, dict) else None
    return isinstance(marts, dict) and bool(marts)


def select_repair_realty_marts() -> set[str]:
    """Marts that are currently stale/error and should be repaired."""
    try:
        from app.audit import realty_marts_status  # noqa: PLC0415
    except Exception:  # noqa: BLE001
        return set()
    try:
        marts = realty_marts_status()
    except Exception:  # noqa: BLE001
        return set()
    if marts.empty or "status" not in marts or "mart" not in marts:
        return set()
    repair = marts[marts["status"].isin(["stale", "error"])]
    return set(repair["mart"].dropna().astype(str))


def build_realty_marts(only: set[str] | None = None) -> bool:
    """Пересобирает быстрые витрины для Streamlit из raw realty-файлов."""
    _print(f"\n{'─'*60}")
    if only:
        _print("⚙️  Частичная сборка realty-витрин для дашборда")
        _print(f"   only: {', '.join(sorted(only))}")
    else:
        _print("⚙️  Сборка realty-витрин для дашборда")
    _print(f"{'─'*60}")
    cmd = [sys.executable, "-m", "pipeline.build_realty_marts", "--strict"]
    if only:
        cmd.extend(["--only", *sorted(only)])
    proc = subprocess.run(
        cmd,
        cwd=ROOT,
        text=True,
        encoding="utf-8",
        errors="replace",
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        check=False,
    )
    if proc.stdout:
        for line in proc.stdout.splitlines():
            _print(f"[marts] {line}")
    if proc.returncode == 0:
        _print("✅ realty-витрины собраны")
        return True
    _print(f"❌ realty-витрины: код выхода {proc.returncode}")
    return False


def collect_site_dates() -> dict:
    """Собирает «дата последнего обновления на сайте» из state-файлов.

    Возвращает {source: {indicator/key: date_str}} для:
      fedstat — fedstat_state.json: {indicator_id: «12.05.2026»}
      rosstat — rosstat_state.json: {key: {date, filename, url}}
      nashdom rasprodannost — nashdom_state.json → state['rasprodannost']
    """
    out: dict = {}
    for state_file, key in [
        (ROOT / "fedstat_state.json", "fedstat"),
        (ROOT / "rosstat_state.json", "rosstat"),
        (ROOT / "state" / "nashdom_state.json", "nashdom"),
        (ROOT / "state" / "erzrf_state.json", "erzrf"),
    ]:
        data = load_json_state(state_file, label=key)
        if data:
            out[key] = data
    return out


def format_site_dates(site_dates: dict) -> list[str]:
    """Превращает state-данные в короткие строки для TDM-сводки."""
    lines: list[str] = []

    # fedstat: {indicator_id: «12.05.2026»}
    fed = site_dates.get("fedstat") or {}
    if isinstance(fed, dict) and fed:
        # Берём «латест» как тот что чаще встречается и пишем 2-3 примера
        dates = [v for v in fed.values() if isinstance(v, str)]
        if dates:
            uniq = sorted(set(dates))
            sample = ", ".join(uniq[-3:])
            lines.append(f"  · fedstat обновлён: {sample}")

    # rosstat: {key: {date, filename, url}}
    ros = site_dates.get("rosstat") or {}
    if isinstance(ros, dict) and ros:
        dates = [v.get("date") for v in ros.values()
                 if isinstance(v, dict) and v.get("date")]
        if dates:
            uniq = sorted(set(dates))
            sample = ", ".join(uniq[-3:])
            lines.append(f"  · rosstat обновлён: {sample}")

    # nashdom: state['rasprodannost'].get('report_period')
    nd = site_dates.get("nashdom") or {}
    if isinstance(nd, dict):
        for sub_key, label in [("monitoring_2_0", "monitoring"),
                                ("rasprodannost", "rasprod"),
                                ("kvartirografia", "kvart")]:
            sub = nd.get(sub_key)
            if isinstance(sub, dict):
                period = (sub.get("report_period")
                          or sub.get("scraped_at", "")[:10])
                if period:
                    lines.append(f"  · nashdom/{label}: {period}")

    # erzrf
    erz = site_dates.get("erzrf") or {}
    if isinstance(erz, dict):
        sub = erz.get("erzrf_top") or {}
        if isinstance(sub, dict):
            when = sub.get("last_run", "")[:10]
            if when:
                lines.append(f"  · erzrf обновлён: {when}")
    return lines


def build_tdm_report(successes: list[str], failures: list[str],
                     diff: dict, total_min: float,
                     deduped: int = 0,
                     site_dates: dict | None = None,
                     marts_ok: bool = True,
                     final_archive_ok: bool = True) -> str:
    """Формирует текст сводки для TDM."""
    icon = "✅" if not failures and marts_ok and final_archive_ok else "⚠️"
    today = datetime.now().strftime("%d.%m.%Y %H:%M")
    lines = [
        f"{icon} **Прогон realty** {today} (за {total_min:.1f} мин)",
        f"Источники: {len(successes)}/{len(successes) + len(failures)} ОК",
    ]
    if successes:
        lines.append(f"✓ OK: {', '.join(successes)}")
    if failures:
        lines.append(f"✗ FAIL: {', '.join(failures)}")
    if not marts_ok:
        lines.append("Marts build failed")
    if not final_archive_ok:
        lines.append("Final archive failed")

    added = diff.get("added", [])
    changed = diff.get("changed", [])
    # Группируем добавленные и изменённые в одну корзину «обновилось»
    updated_topics = summarize_by_topic(added + changed)
    if updated_topics:
        lines.append("")
        lines.append("📥 **Обновилось:**")
        for t in updated_topics:
            lines.append(f"  • {t}")
    else:
        lines.append("")
        lines.append("ℹ️ Новых данных нет — все источники без изменений")

    if site_dates:
        date_lines = format_site_dates(site_dates)
        if date_lines:
            lines.append("")
            lines.append("📅 **Дата данных на сайтах:**")
            lines.extend(date_lines)

    return "\n".join(lines)


def is_monday() -> bool:
    return datetime.now().weekday() == 0


def realty_update_exit_code(*, failures: list[str], marts_ok: bool, final_archive_ok: bool) -> int:
    return 0 if not failures and marts_ok and final_archive_ok else 2


def realty_update_error_message(
    *,
    failures: list[str],
    marts_ok: bool,
    final_archive_ok: bool,
) -> str:
    parts: list[str] = []
    if failures:
        parts.append(f"source failures: {', '.join(failures)}")
    if not marts_ok:
        parts.append("realty marts failed")
    if not final_archive_ok:
        parts.append("final archive failed")
    return "; ".join(parts)


def expand_requested_sources(requested: list[str]) -> tuple[list[str], list[str]]:
    """Раскрывает группы источников в aliases, сохраняя порядок."""
    sources: list[str] = []
    unknown: list[str] = []
    for source in requested:
        if source in GROUP_MAP:
            for sub in GROUP_MAP[source]:
                if sub not in sources:
                    sources.append(sub)
        elif source in SOURCE_MAP:
            if source not in sources:
                sources.append(source)
        else:
            unknown.append(source)
    return sources, unknown


def print_update_plan(sources: list[str], env: dict, *, no_marts: bool) -> None:
    """Печатает быстрый план без запуска скачивателей."""
    print("\nПлан realty-прогона")
    print("=" * 60)
    print(f"Источники: {', '.join(sources)}")
    print(f"KVART_PER_DEV={env.get('KVART_PER_DEV', '0')}")
    print(f"RASPROD_FULL_HISTORY={env.get('RASPROD_FULL_HISTORY', '0')}")
    print(f"SELENIUM_SLEEP_SCALE={env.get('SELENIUM_SLEEP_SCALE', '1')}")
    print("")
    for i, wave in enumerate(WAVES_DEFAULT, 1):
        planned = [alias for alias in wave if alias in sources]
        if planned:
            print(f"Волна {i}: {', '.join(planned)}")
    print("")
    if no_marts:
        print("Realty-витрины: пропущены (--no-marts)")
    else:
        marts = select_realty_marts_for_sources(sources)
        repair_marts = select_repair_realty_marts()
        if marts is not None:
            marts.update(repair_marts)
        if marts is None:
            print("Realty-витрины: полный bootstrap-build (manifest отсутствует)")
        elif marts:
            print(f"Realty-витрины: {', '.join(sorted(marts))}")
        else:
            print("Realty-витрины: не затронуты")
        if repair_marts:
            print(f"Repair-витрины: {', '.join(sorted(repair_marts))}")
    print("=" * 60)


def main():
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("sources", nargs="*", default=["all"],
                        help="Список источников или групп: monitoring, rasprod, kvart, "
                             "erz-top, erz-cards, nashdom, erzrf, all")
    parser.add_argument("--no-archive", action="store_true",
                        help="Не архивировать старые файлы после прогона")
    parser.add_argument("--skip-kvart-per-dev", action="store_true",
                        help="Пропустить долгий per-dev обход квартирографии "
                             "(KVART_PER_DEV=0)")
    parser.add_argument("--weekly-kvart-per-dev", action="store_true",
                        help="Per-dev обход только по понедельникам "
                             "(для daily cron — экономит ~90 мин/день)")
    parser.add_argument("--keep", type=int, default=1,
                        help="Сколько свежих файлов оставить в активной папке (default: 1)")
    parser.add_argument("--no-notify", action="store_true",
                        help="Не отправлять уведомление в TDM")
    parser.add_argument("--no-marts", action="store_true",
                        help="Не пересобирать data/marts/realty после прогона")
    parser.add_argument("--plan", action="store_true",
                        help="Показать план источников/волн/витрин и выйти без запуска")
    parser.add_argument("--full-rasprod-history", action="store_true",
                        help="Для rasprodannost перекачать всю историю, а не "
                             "только новые периоды и самый свежий месяц")
    parser.add_argument("--force", action="store_true",
                        help="Передать --force в чекеры (игнорировать state, "
                             "пере-скачать всё; для rasprodannost включает "
                             "--full-rasprod-history)")
    parser.add_argument("--retries", type=int, default=2,
                        help="Сколько раз повторять упавшие источники "
                             "(default: 2; задержка 30/60с между раундами)")
    args = parser.parse_args()

    # Разворачиваем группы в отдельные источники
    sources, unknown = expand_requested_sources(args.sources)
    if unknown:
        print(f"❌ Неизвестные источники: {', '.join(unknown)}")
        print("   Доступно: " + ", ".join(sorted(set(SOURCE_MAP) | set(GROUP_MAP))))
        return 1

    if not sources:
        parser.print_help()
        return 1

    # Подготовка env (KVART_PER_DEV)
    env = os.environ.copy()
    if args.skip_kvart_per_dev:
        env["KVART_PER_DEV"] = "0"
    elif args.weekly_kvart_per_dev:
        env["KVART_PER_DEV"] = "1" if is_monday() else "0"
        if not is_monday():
            print("ℹ️  --weekly-kvart-per-dev: сегодня не понедельник → "
                  "KVART_PER_DEV=0 (per-dev пропустится, агрегаты остаются)")
    if args.full_rasprod_history or args.force:
        env["RASPROD_FULL_HISTORY"] = "1"
    env.setdefault("SELENIUM_SLEEP_SCALE", "0.8")

    if args.plan:
        print_update_plan(sources, env, no_marts=args.no_marts)
        return 0

    if not acquire_realty_update_lock():
        print(f"update_realty.py: another realty update is already running ({REALTY_UPDATE_LOCK})")
        return 0
    atexit.register(release_realty_update_lock)

    started = time.time()
    log_path = _setup_logging()

    _print(f"\n{'='*60}")
    _print(f"Прогон realty | старт {datetime.now().strftime('%d.%m.%Y %H:%M:%S')}")
    _print(f"Источники: {', '.join(sources)}")
    _print(f"KVART_PER_DEV={env.get('KVART_PER_DEV', '0')}")
    _print(f"RASPROD_FULL_HISTORY={env.get('RASPROD_FULL_HISTORY', '0')}")
    _print(f"SELENIUM_SLEEP_SCALE={env.get('SELENIUM_SLEEP_SCALE', '1')}")
    _print(f"Лог-файл: {log_path}")
    _print(f"{'='*60}")
    successes, failures = [], []
    do_archive = not args.no_archive
    run_meta = {
        "archive": not args.no_archive,
        "keep": args.keep,
        "force": args.force,
        "full_rasprod_history": bool(args.full_rasprod_history or args.force),
        "selenium_sleep_scale": env.get("SELENIUM_SLEEP_SCALE", "1"),
    }
    write_realty_run_status(
        "running",
        started=started,
        sources=sources,
        log_path=log_path,
        completed_sources=[],
        pending_sources=sources,
        **run_meta,
    )
    set_active_realty_run(
        started=started,
        sources=sources,
        log_path=log_path,
        successes=successes,
        failures=failures,
        **run_meta,
    )

    # SNAPSHOT ДО прогона
    before = snapshot_files()

    def _run_wave(wave: list[str], label: str) -> None:
        """Запускает источники волны параллельно, до PARALLEL_LIMIT одновременно."""
        wave = [a for a in wave if a in sources]
        if not wave:
            return
        _print(f"\n{'═'*60}")
        _print(f"{label}: {', '.join(wave)} (max parallel = {PARALLEL_LIMIT})")
        _print(f"{'═'*60}")
        with ThreadPoolExecutor(max_workers=PARALLEL_LIMIT) as pool:
            futures = {
                pool.submit(run_source, alias, env,
                            force=args.force, keep=args.keep,
                            do_archive=do_archive): alias
                for alias in wave
            }
            for f in as_completed(futures):
                alias = futures[f]
                try:
                    ok = f.result()
                except Exception as exc:  # noqa: BLE001
                    _print(f"❌ {alias}: непредвиденная ошибка — {exc}")
                    ok = False
                (successes if ok else failures).append(alias)
                write_active_realty_run_progress(
                    current_stage=label,
                    last_completed_source=alias,
                    last_completed_ok=ok,
                )

    try:
        # Первый проход — волнами (внутри волны параллельно)
        for i, wave in enumerate(WAVES_DEFAULT, 1):
            _run_wave(wave, f"══ Волна {i}/{len(WAVES_DEFAULT)}")

        # Retry упавших — тоже волнами, чтобы erz-cards не стартовал
        # пока erz-top в retry не закончил.
        for retry_round in range(1, args.retries + 1):
            if not failures:
                break
            stuck = set(failures)
            wait_s = 30 * retry_round
            _print(f"\n{'─'*60}")
            _print(f"🔁 Retry #{retry_round}: жду {wait_s}с и повторяю "
                  f"{len(stuck)} источников: {', '.join(sorted(stuck))}")
            _print(f"{'─'*60}")
            time.sleep(wait_s)
            failures.clear()
            for i, wave in enumerate(WAVES_DEFAULT, 1):
                wave_retry = [a for a in wave if a in stuck]
                if not wave_retry:
                    continue
                _run_wave(wave_retry, f"   Retry #{retry_round} волна {i}")
    except KeyboardInterrupt:
        _print(f"\n\n⚠️  Прогон прерван. Готово: {len(successes)} из {len(sources)}")
        write_realty_run_status(
            "interrupted",
            started=started,
            sources=sources,
            log_path=log_path,
            successes=successes,
            failures=failures,
            **run_meta,
        )
        clear_active_realty_run()
        _close_logging()
        release_realty_update_lock()
        sys.exit(130)

    # Дедупликация (safety-net): на этом этапе всё уже было дедуплицировано
    # per-source внутри run_source, но если что-то осталось — добьём.
    deduped_count, real_new_count = deduplicate_new_files(before)

    # SNAPSHOT ПОСЛЕ прогона и дедупликации (до архивирования).
    after = snapshot_files()
    diff = diff_snapshots(before, after)

    # Архивирование
    final_archive_ok = True
    if not args.no_archive:
        final_archive_ok = archive_old(keep=args.keep)

    # Эскроу-подсказка
    check_escrow()

    marts_ok = True
    marts_selected: set[str] | None = set()
    if not args.no_marts:
        changed_for_marts = diff["added"] + diff["changed"]
        marts_changed_aliases = sorted({
            alias for path in changed_for_marts
            if (alias := source_alias_for_changed_path(path)) is not None
        })
        marts_repair_selected = sorted(select_repair_realty_marts())
        marts_selected = select_realty_marts_for_changes(changed_for_marts)
        if marts_selected == set():
            _print(f"\n{'─'*60}")
            _print("⚙️  Realty-витрины: нет затронутых источников, сборка пропущена")
            _print(f"{'─'*60}")
        else:
            marts_ok = build_realty_marts(only=marts_selected)
    else:
        changed_for_marts = []
        marts_changed_aliases = []
        marts_repair_selected = []

    total_min = (time.time() - started) / 60
    _print(f"\n{'='*60}")
    _print(f"ИТОГ за {total_min:.1f} мин:")
    _print(f"  ✅ Успешно: {len(successes)} — {', '.join(successes) if successes else '—'}")
    if failures:
        _print(f"  ❌ Ошибки:  {len(failures)} — {', '.join(failures)}")
    if diff["added"]:
        _print(f"  📥 Новых файлов:    {len(diff['added'])}")
    if diff["changed"]:
        _print(f"  ✎  Обновлено:       {len(diff['changed'])}")
    if deduped_count:
        _print(f"  ↩️  Дублей удалено:  {deduped_count}")
    if not marts_ok:
        _print("  ⚠️  Витрины сайта: ошибка сборки")
    if not final_archive_ok:
        _print("  ⚠️  Final archive: failed")
    _print(f"{'='*60}\n")
    _print(f"📁 Полный лог сохранён: {log_path}")

    write_realty_run_status(
        "success" if not failures and marts_ok and final_archive_ok else "failed",
        started=started,
        sources=sources,
        log_path=log_path,
        successes=successes,
        failures=failures,
        marts_ok=marts_ok,
        marts_selected=None if marts_selected is None else sorted(marts_selected),
        marts_changed_aliases=marts_changed_aliases,
        marts_changed_paths=changed_for_marts,
        marts_repair_selected=marts_repair_selected,
        final_archive_ok=final_archive_ok,
        error=realty_update_error_message(
            failures=failures,
            marts_ok=marts_ok,
            final_archive_ok=final_archive_ok,
        ),
        **run_meta,
        deduped_count=deduped_count,
        real_new_count=real_new_count,
        diff=diff,
    )

    # === Уведомление в TDM ===
    if not args.no_notify:
        try:
            sys.path.insert(0, str(ROOT))
            from pipeline.tdm_notify import notify
            text = build_tdm_report(successes, failures, diff, total_min,
                                    deduped=deduped_count,
                                    site_dates=collect_site_dates(),
                                    marts_ok=marts_ok,
                                    final_archive_ok=final_archive_ok)
            notify(text, silent=True)
        except Exception:  # noqa: BLE001
            pass

    clear_active_realty_run()
    _close_logging()
    release_realty_update_lock()
    return realty_update_exit_code(
        failures=failures,
        marts_ok=marts_ok,
        final_archive_ok=final_archive_ok,
    )


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:  # noqa: BLE001
        _print(f"❌ update_realty.py: unexpected failure — {type(exc).__name__}: {exc}")
        mark_active_realty_run_failed(exc)
        clear_active_realty_run()
        _close_logging()
        release_realty_update_lock()
        sys.exit(2)
