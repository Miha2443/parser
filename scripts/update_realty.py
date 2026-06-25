"""Полный прогон по всем источникам недвижимости с автоархивированием.

Запуск:
    py scripts/update_realty.py              # всё
    py scripts/update_realty.py monitoring   # один источник
    py scripts/update_realty.py --no-archive # без архивирования старого
    py scripts/update_realty.py --skip-kvart-per-dev  # без долгого per-dev обхода
    py scripts/update_realty.py --weekly-kvart-per-dev  # per-dev только по понедельникам

Порядок выполнения:
  1. nashdom_checker monitoring_2_0   (~1 мин, requests)
  2. nashdom_checker rasprodannost    (~15-30 мин, selenium по всем регионам)
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
import hashlib
import os
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
REALTY_ROOT = ROOT / "data" / "raw" / "realty"
LOG_DIR = ROOT / "logs"

# Глобальный файл лога текущего прогона. Инициализируется в main().
_LOG_FILE: Path | None = None
_LOG_FH = None

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
    "rasprod":     90,
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

# Параллельный пул для волн (можно урезать через env PARALLEL_LIMIT=2).
PARALLEL_LIMIT = max(1, int(os.environ.get("PARALLEL_LIMIT", "4")))

# Волны: внутри волны источники запускаются параллельно, между волнами —
# последовательно (erz-cards зависит от top_developers_*.json от erz-top).
# fedstat вынесен в отдельную волну: на Chrome 149 fedstat-фронт стабильно
# падает с `appendChild on null` при параллельной нагрузке (4 Chrome'а
# делят CPU/GPU, рейс в инициализации JS). Соло — работает.
WAVES_DEFAULT = [
    ["monitoring", "rasprod", "kvart", "erz-top", "rosstat"],
    ["erz-cards"],
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
    (REALTY_ROOT, "realty"),
    (ROOT / "downloads", "downloads"),
]


def snapshot_files() -> dict[str, tuple[int, str]]:
    """Snapshot файлов realty/ + downloads/: {prefix:rel_path → (size, sha256_head)}.

    Хеш по первым 256 КБ — быстро и достаточно для детекта изменений.
    """
    out: dict[str, tuple[int, str]] = {}
    for base, prefix in SNAPSHOT_DIRS:
        if not base.exists():
            continue
        for f in base.rglob("*"):
            if not f.is_file() or "_archive" in f.parts:
                continue
            try:
                size = f.stat().st_size
                h = hashlib.sha256()
                with f.open("rb") as fh:
                    h.update(fh.read(256 * 1024))
                rel = str(f.relative_to(base)).replace("\\", "/")
                out[f"{prefix}:{rel}"] = (size, h.hexdigest()[:16])
            except OSError:
                pass
    return out


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


def deduplicate_new_files(before_snapshot: dict) -> tuple[int, int]:
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

    after = snapshot_files()
    added_paths = sorted(set(after) - set(before_snapshot))
    if not added_paths:
        return 0, 0
    _print(f"\n{'─'*60}")
    _print(f"🔍 Дедупликация новых файлов ({len(added_paths)} шт)")
    _print(f"{'─'*60}")
    deduped = 0
    real_new = 0
    prefix_to_base = {p: b for b, p in SNAPSHOT_DIRS}
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
    if force and alias in ("fedstat", "rosstat"):
        extra.append("--force")
    cmd = [sys.executable, "-u", str(ROOT / script), *args, *extra]
    timeout_min = SOURCE_TIMEOUT_MIN.get(alias, DEFAULT_TIMEOUT_MIN)
    prefix = f"[{alias:>10}]"

    _print(f"\n{'─'*60}")
    _print(f"▶ {alias} (watchdog: {timeout_min}мин)")
    _print(f"{'─'*60}")

    # Snapshot до источника — для дедупликации только его файлов.
    before_src = snapshot_files() if do_archive else {}

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
                deduplicate_new_files(before_src)
            except Exception as exc:  # noqa: BLE001
                _print(f"⚠️  {alias}: дедупликация упала — {exc}")
            archive_old_for_source(alias, keep=keep)
        return True
    _print(f"❌ {alias}: код выхода {rc} (за {elapsed/60:.1f} мин)")
    return False


def archive_old(keep: int = 1) -> None:
    """Перемещает устаревшие выгрузки в _archive/<date>/ (полная зачистка)."""
    cmd = [sys.executable, "-m", "pipeline.archive_old", "--keep", str(keep)]
    _print(f"\n{'─'*60}")
    _print(f"📦 Финальная архивация (safety-net, keep={keep})")
    _print(f"{'─'*60}")
    subprocess.run(cmd, cwd=ROOT, check=False)


def archive_old_for_source(alias: str, keep: int = 1) -> None:
    """Архивирует устаревшие файлы только этого источника.

    Принцип: для каждого пути из SOURCE_ARCHIVE_PATHS[alias] зовём
    pipeline.archive_old с --paths и --prefixes — это ограничивает
    архивацию только семействами alias'а (важно для nashdom, где
    monitoring_2_0_*, rasprodannost_* и kvartirografia_* лежат вместе).
    """
    paths = SOURCE_ARCHIVE_PATHS.get(alias)
    prefixes = SOURCE_PREFIXES.get(alias)
    if not paths or not prefixes:
        return
    paths_arg = [f"realty/{p}" for p in paths]
    cmd = [
        sys.executable, "-m", "pipeline.archive_old",
        "--keep", str(keep),
        "--paths", *paths_arg,
        "--prefixes", *prefixes,
    ]
    subprocess.run(cmd, cwd=ROOT, check=False)


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
        if not state_file.is_file():
            continue
        try:
            import json as _j
            data = _j.loads(state_file.read_text(encoding="utf-8"))
            out[key] = data
        except (OSError, ValueError):
            pass
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
                     site_dates: dict | None = None) -> str:
    """Формирует текст сводки для TDM."""
    icon = "✅" if not failures else "⚠️"
    today = datetime.now().strftime("%d.%m.%Y %H:%M")
    lines = [
        f"{icon} **Прогон realty** {today} (за {total_min:.1f} мин)",
        f"Источники: {len(successes)}/{len(successes) + len(failures)} ОК",
    ]
    if successes:
        lines.append(f"✓ OK: {', '.join(successes)}")
    if failures:
        lines.append(f"✗ FAIL: {', '.join(failures)}")

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
    parser.add_argument("--force", action="store_true",
                        help="Передать --force в чекеры (игнорировать state, "
                             "пере-скачать всё)")
    parser.add_argument("--retries", type=int, default=2,
                        help="Сколько раз повторять упавшие источники "
                             "(default: 2; задержка 30/60с между раундами)")
    args = parser.parse_args()

    # Разворачиваем группы в отдельные источники
    sources: list[str] = []
    for s in args.sources:
        if s in GROUP_MAP:
            for sub in GROUP_MAP[s]:
                if sub not in sources:
                    sources.append(sub)
        elif s in SOURCE_MAP:
            if s not in sources:
                sources.append(s)
        else:
            print(f"⚠️  Игнорирую неизвестный аргумент: {s}")

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

    log_path = _setup_logging()

    _print(f"\n{'='*60}")
    _print(f"Прогон realty | старт {datetime.now().strftime('%d.%m.%Y %H:%M:%S')}")
    _print(f"Источники: {', '.join(sources)}")
    _print(f"KVART_PER_DEV={env.get('KVART_PER_DEV', '0')}")
    _print(f"Лог-файл: {log_path}")
    _print(f"{'='*60}")

    # SNAPSHOT ДО прогона
    before = snapshot_files()

    started = time.time()
    successes, failures = [], []
    do_archive = not args.no_archive

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
            failures = []
            for i, wave in enumerate(WAVES_DEFAULT, 1):
                wave_retry = [a for a in wave if a in stuck]
                if not wave_retry:
                    continue
                _run_wave(wave_retry, f"   Retry #{retry_round} волна {i}")
    except KeyboardInterrupt:
        _print(f"\n\n⚠️  Прогон прерван. Готово: {len(successes)} из {len(sources)}")
        _close_logging()
        sys.exit(130)

    # Дедупликация (safety-net): на этом этапе всё уже было дедуплицировано
    # per-source внутри run_source, но если что-то осталось — добьём.
    deduped_count, real_new_count = deduplicate_new_files(before)

    # SNAPSHOT ПОСЛЕ прогона и дедупликации (до архивирования).
    after = snapshot_files()
    diff = diff_snapshots(before, after)

    # Архивирование
    if not args.no_archive:
        archive_old(keep=args.keep)

    # Эскроу-подсказка
    check_escrow()

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
    _print(f"{'='*60}\n")
    _print(f"📁 Полный лог сохранён: {log_path}")

    # === Уведомление в TDM ===
    if not args.no_notify:
        try:
            sys.path.insert(0, str(ROOT))
            from pipeline.tdm_notify import notify
            text = build_tdm_report(successes, failures, diff, total_min,
                                    deduped=deduped_count,
                                    site_dates=collect_site_dates())
            notify(text, silent=True)
        except Exception:  # noqa: BLE001
            pass

    _close_logging()
    return 0 if not failures else 2


if __name__ == "__main__":
    sys.exit(main())
