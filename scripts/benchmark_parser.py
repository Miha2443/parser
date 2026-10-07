"""Offline, read-only benchmark of existing realty marts; never an ETL/UI timer.

Run: python -B scripts/benchmark_parser.py --repeats 5
Workers use a temporary snapshot, deny writes/network/process creation, and emit
only JSON on stdout. Only trusted repository pickle files may be benchmarked.
"""
from __future__ import annotations

import argparse
import csv
import ctypes
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import platform
import shutil
import statistics
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone

ROOT = Path(__file__).resolve().parent.parent
MARTS = (
    "kvartirografia", "monitoring_2_0", "erzrf_top", "erzrf_cards",
    "escrow_manual", "rasprodannost", "vvod_static", "emiss_34118",
)
MODES = ("pickle_new_process", "loader_new_process", "loader_warm_memory")


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def within(root: Path, relative: str) -> Path:
    candidate = (root / relative).resolve()
    if not candidate.is_relative_to(root.resolve()):
        raise ValueError(f"Path escapes snapshot root: {relative}")
    return candidate


def write_csv(path: Path, rows: list[dict]) -> None:
    fields = list(dict.fromkeys(key for row in rows for key in row))
    with path.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def snapshot(root: Path, destination: Path, marts: tuple[str, ...]) -> list[dict]:
    manifest_path = root / "data/marts/realty/manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    roles = {"data/marts/realty/manifest.json": "manifest"}
    for name in marts:
        entry = manifest["marts"][name]
        roles[f"data/marts/realty/{name}.pkl"] = "mart"
        for source in entry.get("sources", []):
            relative = within(root, source["path"]).relative_to(root).as_posix()
            roles[relative] = "raw_manifest_reference"
    for directory in ("app", "pipeline"):
        for source in (root / directory).rglob("*.py"):
            roles[source.relative_to(root).as_posix()] = "source"
    for relative in ("scripts/benchmark_parser.py", "requirements.txt"):
        if (root / relative).is_file():
            roles[relative] = "source"
    inventory = []
    for relative, role in sorted(roles.items()):
        original = within(root, relative)
        target = within(destination, relative)
        if not original.is_file():
            raise FileNotFoundError(f"Missing snapshot input: {relative}")
        before = sha256(original)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(original, target)
        if sha256(target) != before or sha256(original) != before:
            raise RuntimeError(f"Input changed while copying: {relative}")
        inventory.append({
            "path": relative, "role": role, "size_bytes": original.stat().st_size,
            "mtime_ns": original.stat().st_mtime_ns, "sha256": before,
        })
    return inventory


def worker_guard() -> list[tuple[str, str]]:
    """Defence against accidental side effects; not a hostile-code sandbox."""
    violations: list[tuple[str, str]] = []
    mutations = {
        "os.remove", "os.rename", "os.rmdir", "os.mkdir", "os.chmod", "os.utime",
        "os.link", "os.symlink", "os.truncate", "shutil.copyfile", "os.system",
        "subprocess.Popen", "os.exec", "os.posix_spawn", "os.startfile",
        "socket.connect", "socket.connect_ex", "socket.bind", "socket.getaddrinfo",
        "socket.sendto", "socket.sendmsg",
    }

    def audit(event, args):
        blocked = event in mutations
        if event == "open":
            mode, flags = args[1], args[2]
            blocked = (isinstance(mode, str) and any(c in mode for c in "wax+")) or (
                isinstance(flags, int) and bool(flags & (
                    os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC | os.O_APPEND
                ))
            )
        if blocked:
            violations.append((event, str(args[0])[:200] if args else ""))
            raise PermissionError(f"Benchmark worker denies side effect: {event}")

    sys.addaudithook(audit)
    return violations


def memory() -> dict:
    """OS process high water mark, including imports and prior warm iterations."""
    if os.name == "nt":
        from ctypes import wintypes

        class Counters(ctypes.Structure):
            _fields_ = [("cb", wintypes.DWORD), ("PageFaultCount", wintypes.DWORD)] + [
                (name, ctypes.c_size_t) for name in (
                    "PeakWorkingSetSize", "WorkingSetSize", "QuotaPeakPagedPoolUsage",
                    "QuotaPagedPoolUsage", "QuotaPeakNonPagedPoolUsage", "QuotaNonPagedPoolUsage",
                    "PagefileUsage", "PeakPagefileUsage",
                )
            ]

        current = ctypes.windll.kernel32.GetCurrentProcess
        current.restype = wintypes.HANDLE
        read = ctypes.windll.psapi.GetProcessMemoryInfo
        read.argtypes = [wintypes.HANDLE, ctypes.POINTER(Counters), wintypes.DWORD]
        counters = Counters()
        counters.cb = ctypes.sizeof(counters)
        if read(current(), ctypes.byref(counters), counters.cb):
            return {"process_peak_rss_bytes": counters.PeakWorkingSetSize,
                    "process_rss_bytes": counters.WorkingSetSize,
                    "memory_method": "Windows GetProcessMemoryInfo; process-lifetime high-water"}
    else:
        try:
            import resource
            scale = 1 if sys.platform == "darwin" else 1024
            return {"process_peak_rss_bytes": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss * scale,
                    "process_rss_bytes": "", "memory_method": "getrusage; process-lifetime high-water"}
        except ImportError:
            pass
    return {"process_peak_rss_bytes": "", "process_rss_bytes": "", "memory_method": "unavailable"}


def frame_sizes(value, prefix="root") -> dict:
    import pandas as pd
    if isinstance(value, pd.DataFrame):
        return {prefix: {"rows": len(value), "columns": len(value.columns)}}
    frames = {}
    if isinstance(value, dict):
        for key, nested in value.items():
            frames.update(frame_sizes(nested, f"{prefix}.{key}"))
    return frames


def worker(args) -> int:
    import logging
    logging.disable(logging.CRITICAL)
    sys.dont_write_bytecode = True
    os.environ["PARSER_USE_REALTY_MARTS"] = "1"
    os.environ["PARSER_REQUIRE_REALTY_MARTS"] = "1"
    os.environ["STREAMLIT_BROWSER_GATHER_USAGE_STATS"] = "false"
    sys.path.insert(0, str(ROOT))
    violations = worker_guard()
    import pandas as pd
    if args.mode == "pickle_new_process":
        load = lambda: pd.read_pickle(ROOT / f"data/marts/realty/{args.mart}.pkl")
    else:
        from app import data_access
        load = getattr(data_access, f"load_{args.mart}")
    rows = []
    try:
        if args.mode == "loader_warm_memory":
            # No clear() touches another process: this worker owns its memory cache.
            load()
        for repetition in range(1, args.worker_repeats + 1):
            wall_start, cpu_start = time.perf_counter(), time.process_time()
            value = load()
            cpu_seconds = time.process_time() - cpu_start
            wall_seconds = time.perf_counter() - wall_start
            counters = memory()
            frames = frame_sizes(value)
            rows.append({
                "repetition": repetition, "wall_seconds": wall_seconds,
                "cpu_seconds": cpu_seconds, **counters,
                "dataframe_rows_sum": sum(frame["rows"] for frame in frames.values()),
                "frames_json": json.dumps(frames, ensure_ascii=False, sort_keys=True),
                "status": "ok", "error": "",
            })
            del value
    except Exception as exc:  # Store failed runs instead of hiding them in the median.
        rows.append({"repetition": len(rows) + 1, "status": "error",
                     "error": f"{type(exc).__name__}: {exc}"})
    if violations:
        for row in rows:
            row.update(status="error", error=f"Blocked side effects: {violations}")
    print(json.dumps(rows, ensure_ascii=True))
    return 0 if rows and all(row["status"] == "ok" for row in rows) else 1


def directory_sizes(root: Path) -> list[dict]:
    # Categories overlap (e.g. an archive under raw); they must not be summed.
    directories = {root / name for name in (
        "data/raw", "data/processed", "data/derived", "data/marts", "downloads",
        "nashdom", "erzrf", "state", "outputs",
    )}
    directories.update(path for path in root.rglob("*") if path.is_dir()
                       and path.name.lower() in {"archive", "archives", "архив"}
                       and ".git" not in path.parts)
    result = []
    for directory in sorted(directories):
        files = [path for path in directory.rglob("*") if path.is_file()] if directory.exists() else []
        result.append({"path": directory.relative_to(root).as_posix(), "exists": directory.exists(),
                       "files": len(files), "bytes": sum(path.stat().st_size for path in files)})
    return result


def environment(root: Path) -> dict:
    def git(*arguments):
        result = subprocess.run(["git", "-C", str(root), *arguments], capture_output=True, text=True)
        return result.stdout.strip() if result.returncode == 0 else "unavailable"

    physical_ram = None
    chrome = {}
    if os.name == "nt":
        class MemoryStatus(ctypes.Structure):
            _fields_ = [("length", ctypes.c_ulong), ("load", ctypes.c_ulong)] + [
                (name, ctypes.c_ulonglong) for name in (
                    "total_phys", "avail_phys", "total_page", "avail_page", "total_virtual",
                    "avail_virtual", "avail_extended",
                )
            ]
        status = MemoryStatus()
        status.length = ctypes.sizeof(status)
        if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):
            physical_ram = status.total_phys
        import winreg
        for hive_name in ("HKEY_CURRENT_USER", "HKEY_LOCAL_MACHINE"):
            try:
                with winreg.OpenKey(getattr(winreg, hive_name), r"SOFTWARE\Google\Chrome\BLBeacon") as key:
                    chrome[hive_name + r"\SOFTWARE\Google\Chrome\BLBeacon"] = winreg.QueryValueEx(key, "version")[0]
            except OSError:
                pass
    disk = shutil.disk_usage(root)
    return {
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "python": sys.version, "python_executable": sys.executable,
        "os": platform.platform(), "machine": platform.machine(), "processor": platform.processor(),
        "logical_cpu_count": os.cpu_count(), "physical_ram_bytes": physical_ram,
        "disk_total_bytes": disk.total, "disk_free_bytes": disk.free,
        "chrome_registry_versions": chrome, "chrome_measured": False,
        "packages": dict(sorted((d.metadata["Name"], d.version) for d in importlib.metadata.distributions()
                                if d.metadata["Name"])),
        "git_head": git("rev-parse", "HEAD"), "git_worktree_dirty": bool(git("status", "--porcelain")),
        "directory_sizes": directory_sizes(root),
    }


def summarize(rows: list[dict]) -> list[dict]:
    summaries = []
    for name in dict.fromkeys(row["mart"] for row in rows):
        for mode in MODES:
            group = [row for row in rows if row["mart"] == name and row["mode"] == mode]
            valid = [row for row in group if row["status"] == "ok"]
            result = {"mart": name, "mode": mode, "observations": len(group),
                      "successful_observations": len(valid), "errors": len(group) - len(valid)}
            for metric in ("wall_seconds", "cpu_seconds", "worker_process_wall_seconds", "process_peak_rss_bytes"):
                values = [float(row[metric]) for row in valid if row.get(metric) not in (None, "")]
                result[f"{metric}_median"] = statistics.median(values) if values else ""
                result[f"{metric}_max"] = max(values) if values else ""
            # Five observations are deliberately not advertised as a p95 estimate.
            summaries.append(result)
    return summaries


METHODOLOGY = """# Локальный baseline чтения витрин

Команда: `python -B scripts/benchmark_parser.py --repeats 5`.
Повторный запуск перезаписывает только `baseline*` в `docs/audit`; для сравнения
задайте отдельный `--output-dir`. Используется Python из команды запуска.

`baseline.csv` содержит отдельные наблюдения; `baseline_summary.csv` — число
успехов/ошибок, медиану и максимум. p95 не рассчитывается по пяти наблюдениям.
`baseline_inputs.csv` фиксирует SHA-256, размеры и mtime снимка исходников,
готовых pickle, manifest и перечисленных в нём raw. `baseline_environment.json`
содержит пакеты, ОС, Python, CPU/RAM/диск, git HEAD, хэш исходников и размеры
каталогов. Версия Chrome из реестра не подтверждает запуск этого браузера.

## Границы измерений

- `pickle_new_process`: новый процесс на повтор; таймер вокруг `pandas.read_pickle`
  готовой витрины, импорты исключены из `wall_seconds` и `cpu_seconds`.
- `loader_new_process`: новый процесс на повтор; первый вызов public loader из
  `app.data_access` со Streamlit cache и обязательной готовой витриной.
  Импорты не входят в таймер операции; первая сериализация cache входит.
- `loader_warm_memory`: отдельный процесс для витрины, один нетаймируемый вызов
  прогрева, затем последовательные повторы public loader с памятью Streamlit.
  Десериализация результата cache входит в время; проверка размера — после таймера.
- `worker_process_wall_seconds` заполняется только для нового процесса: включает
  запуск Python, импорты, audit guard, операцию, сбор формы ответа, JSON и выход.
  Это НЕ время первой страницы; `cpu_seconds` — CPU операции, а не всего worker.
- Новый процесс означает пустой Python/Streamlit cache. ОС file cache не очищается:
  копирование и SHA-256 заранее читают файлы. Физически холодный диск не измерен.
- Память — системный максимум RSS процесса за его жизнь (включая импорты и
  предыдущие тёплые вызовы), не прирост и не пик только одной операции. При
  недоступном системном счётчике поле пустое. Python allocation tracing выключен.
- Сумма строк относится к вложенным DataFrame и может включать пересекающиеся
  представления; это не число уникальных сущностей. Детали — `frames_json`.

## Изоляция и воспроизводимость

Временный снимок сохраняет пути и mtime; копии проверяются по SHA-256. Worker
исполняет копию кода, работает в снимке, использует `-B`, запрет сетевых событий,
создания процессов и записи в файлы через Python audit hook. Любая попытка
помечает результат ошибкой, даже если loader её перехватил. Это защита от
случайных побочных эффектов доверенного кода, а не sandbox для злонамеренного
pickle/native extension. Измеряются только доверенные локальные файлы проекта.
Оригинальные входы перепроверяются после запуска; изменение аннулирует успех
наблюдений и приводит к ненулевому коду возврата. Код worker тоже фиксируется.

Снимок содержит raw только из manifest, а не всю рабочую историю. Отсутствующая
или устаревшая витрина — ошибка; полная обработка raw не является целью. Размеры
каталогов могут пересекаться и не складываются. Темп роста истории требует
нескольких дат наблюдения и пока не определён. Список установленных пакетов —
инвентаризация, не проверенный lockfile зависимостей.

В baseline НЕ входят Selenium/HTTP, обновление источников, ETL, per-dev обход,
рендер Streamlit/Plotly, фильтры, карты, экспорт, сеть пользователя и доставка
ботом. Здесь нет доказательства актуальности/правильности данных, удобства
телефона или ускорения системы. Для сравнения запускать последовательно при
одинаковых входах/среде/режимах; фоновая нагрузка ОС не контролируется. Все
незавершённые проверки перечислены в `progress.md`.
"""


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repeats", type=int, default=5)
    parser.add_argument("--output-dir", type=Path, default=ROOT / "docs/audit")
    parser.add_argument("--marts", nargs="+", choices=MARTS, default=list(MARTS))
    parser.add_argument("--worker", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--mode", choices=MODES, help=argparse.SUPPRESS)
    parser.add_argument("--mart", choices=MARTS, help=argparse.SUPPRESS)
    parser.add_argument("--worker-repeats", type=int, default=1, help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    if args.worker:
        return worker(args)
    if args.repeats < 5:
        parser.error("Use at least 5 repetitions for a baseline")
    output = args.output_dir.resolve()
    protected = [ROOT / name for name in ("data", "state", "downloads", "nashdom", "erzrf")]
    if any(output.is_relative_to(path.resolve()) for path in protected):
        parser.error("Reports must not be written under production data/state directories")
    output.mkdir(parents=True, exist_ok=True)
    info = environment(ROOT)
    info.update(repeats=args.repeats, marts=args.marts, command=sys.argv,
                source_definition="Snapshot of app/**/*.py, pipeline/**/*.py, benchmark and requirements")
    rows = []
    with tempfile.TemporaryDirectory(prefix="parser-baseline-") as temporary:
        snap = Path(temporary)
        inputs = snapshot(ROOT, snap, tuple(args.marts))
        source_records = [(item["path"], item["sha256"]) for item in inputs if item["role"] == "source"]
        source_hash = hashlib.sha256(json.dumps(source_records, sort_keys=True).encode()).hexdigest()
        info["source_snapshot_sha256"] = source_hash
        run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        info["run_id"] = run_id
        input_by_path = {item["path"]: item for item in inputs}
        child_env = os.environ.copy()
        child_env.update(PYTHONDONTWRITEBYTECODE="1", PYTHONHASHSEED="0", PYTHONUTF8="1",
                         HOME=str(snap), USERPROFILE=str(snap), STREAMLIT_BROWSER_GATHER_USAGE_STATS="false",
                         PARSER_ROOT=str(snap), PARSER_DOWNLOADS=str(snap / "downloads"),
                         PARSER_STATE=str(snap / "state"))
        child_env.pop("PYTHONPATH", None)
        for name in args.marts:
            for mode in MODES:
                count = 1 if mode == "loader_warm_memory" else args.repeats
                for index in range(1, count + 1):
                    command = [sys.executable, "-B", str(snap / "scripts/benchmark_parser.py"),
                               "--worker", "--mart", name, "--mode", mode,
                               "--worker-repeats", str(args.repeats if count == 1 else 1)]
                    start = time.perf_counter()
                    try:
                        result = subprocess.run(command, cwd=snap, env=child_env, capture_output=True,
                                                text=True, encoding="utf-8", timeout=120)
                        elapsed = time.perf_counter() - start
                        try:
                            observations = json.loads(result.stdout)
                        except ValueError as exc:
                            raise RuntimeError(f"Worker emitted no valid JSON (exit {result.returncode}): "
                                               f"{result.stderr[-2000:]}") from exc
                        if result.returncode and all(row.get("status") == "ok" for row in observations):
                            raise RuntimeError(f"Worker exit {result.returncode}: {result.stderr[-1000:]}")
                    except (subprocess.TimeoutExpired, ValueError, RuntimeError) as exc:
                        observations = [{"repetition": 1, "status": "error", "error": str(exc)}]
                        elapsed = time.perf_counter() - start
                    mart_input = input_by_path[f"data/marts/realty/{name}.pkl"]
                    for observation in observations:
                        rows.append({"run_id": run_id, "mart": name, "mode": mode,
                                     "source_snapshot_sha256": source_hash, "git_head": info["git_head"],
                                     "input_sha256": mart_input["sha256"], "input_bytes": mart_input["size_bytes"],
                                     "os_file_cache": "uncontrolled; warmed by snapshot/hash",
                                     **observation,
                                     "repetition": observation["repetition"] if count == 1 else index,
                                     "worker_process_wall_seconds": "" if count == 1 else elapsed})
                print(f"{name}: {mode}: finished", flush=True)
        changed = [item["path"] for item in inputs if not (ROOT / item["path"]).is_file()
                   or sha256(ROOT / item["path"]) != item["sha256"]]
        info["original_inputs_changed_during_run"] = changed
        if changed:
            for row in rows:
                row.update(status="invalidated", error="Original inputs/code changed during benchmark")
    write_csv(output / "baseline.csv", rows)
    write_csv(output / "baseline_inputs.csv", inputs)
    write_csv(output / "baseline_summary.csv", summarize(rows))
    (output / "baseline_environment.json").write_text(json.dumps(info, ensure_ascii=False, indent=2), encoding="utf-8")
    (output / "baseline_methodology.md").write_text(METHODOLOGY, encoding="utf-8")
    failures = sum(row["status"] != "ok" for row in rows)
    print(f"{len(rows)} observations; {failures} errors/invalidated; reports: {output}")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
