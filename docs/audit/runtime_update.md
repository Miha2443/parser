# Realty runtime audit and fixes

Scope: `scripts/update_realty.py`, processed build selection, and Windows scheduler wrappers. No scheduled task was registered by this change. Credentials were not requested or written. Existing production status was inspected and hashed, never replaced by test fixtures.

## Findings and behavior

- Status write errors formerly only printed a warning and could leave exit code 0. The writer now returns failure, retains the prior authoritative JSON when replacement fails, and the update exits 2. Failure of the initial status write prevents source execution. A transient heartbeat write failure remains a failed run even if later writes recover.
- Every actual run uses a timestamp with microseconds and a random suffix. Canonical logs are `logs/update_<run_id>.log`, with 20 retained. Latest per-run status is saved in `data/processed/realty_update_runs/run_<run_id>.json`, with 50 retained. Only the tool's generated filename patterns are rotated. History is written before authoritative latest status; the two files are separate atomic replacements, not a transaction. A final status failure therefore leaves a nonzero process exit and the previous authoritative status. Rotation failure is a warning.
- A background heartbeat updates `updated_at`, elapsed duration, stage, active source aliases and completed/pending source lists every 30 seconds while work is running. The heartbeat is stopped before terminal status to prevent a late `running` overwrite. Download waves, retry waiting, snapshots, deduplication, archiving and builds have distinct stages.
- The outer lifecycle handles exceptions and Ctrl+C during snapshot, download, archive and build stages, closes logs, releases its own lock and records `failed` or `interrupted`. Tracked child processes are cancelled on interruption. Forced OS termination or power loss cannot write a terminal record; the last heartbeat remains the evidence for stale-run detection.
- Archive/build child output is included in the canonical run log. `KVART_PER_DEV` now shows the actual default of `1` and the effective value is included in status. `RASPROD_FULL_HISTORY` metadata reflects the environment as well as flags.
- `--no-archive` skips both per-source and final deduplication/archival deletion. `--no-notify` passes `TDM_DISABLED=1` to children; processed orchestration also honors it for its separate Telegram notifier.

## Scoped builds

`pipeline/orchestrator.py --only <indicator IDs>` validates IDs before directory/lock/audit work and processes only matching registry indicators. With no `--only`, all registered indicators retain their previous behavior.

An explicitly scoped updater invocation maps aliases to registry `source` and `source_ids`. For example, `monitoring` builds only processed `realty_monitoring_2_0`. Its mart changes and repair candidates are limited to `monitoring_2_0`; unrelated stale/error marts are not repaired. A missing mart manifest can be bootstrapped using only the selected marts; an existing corrupt/empty manifest still fails unchanged. An `all` invocation retains full processed selection and broad mart repair behavior. The plan output uses the same requested mart boundary.

If per-run history was written but latest-status publication fails, the writer attempts to correct history to `failed`, including persistence errors and `status_published: false`. If both destinations are unavailable, the nonzero exit and canonical log remain the evidence.

An authorized bounded production command, after independent review of the monitoring download guard, is:

```powershell
$env:TDM_DISABLED = '1'
python scripts/update_realty.py monitoring --no-notify --no-archive --retries 0
```

This command downloads/checks monitoring, builds its processed result and, if selected by changed files or repair status, its mart. It is a real update, not a test. It was **not executed as part of the isolated runtime checks**.

## Scheduler

`scripts/register_realty_task.ps1` is the single registration implementation, with:

- Daily local time `06:00`, configurable with `-At HH:mm`.
- `StartWhenAvailable` for missed scheduled starts and `MultipleInstances IgnoreNew`.
- Explicit project working directory and a quoted `cmd /d /s /c` action.
- Default `-LogonMode Interactive`, requiring a logged-in user.
- Explicit `-LogonMode Password` to request account credentials for logged-out execution. Administrator elevation alone does not provide this behavior.
- `-WhatIf` and `-Unregister`; a 12-hour task execution limit.

Both legacy registration BAT entry points delegate to this script and the canonical task name `parser_etl_realty`. `register_scheduler.bat` requests Highest run level; `register_scheduler_user.bat` keeps Limited. Existing tasks with the old `parser_etl_realty_user` name are not automatically deleted or migrated. The machine must be powered on for any task to execute; catch-up is deferred until Windows can run the task.

The scheduled BAT forwards `%*`, preserves the Python exit code, selects the project venv if present, and uses a unique wrapper log in `data/processed/etl_<timestamp>_<guid>.log` (20 retained). BAT files use CRLF. Scheduler deployment and logon mode remain a separate environment decision.

## Validation

All runtime tests used temporary roots for raw, processed, status, history, logs, manifest and locks, with `TDM_DISABLED=1`, `--no-notify`, mocked download/build work and an unexpected-subprocess guard. No heavy downloads, production builds, notifications or task registration occurred.

Passed checks:

- `check_update_realty_runtime.py`: 9 lifecycle cases, including initial/final status failure, old JSON preservation, heartbeat, terminal race, interruption outside waves, failed source/build, scope, unique names, safe history rotation and archive log capture.
- `check_orchestrator_scope.py`: selected/all/unknown-ID paths and alias mapping, with audit/process/notification work mocked.
- `check_update_realty_plan.py`, `check_realty_status_atomic_write.py`, `check_update_realty_lock.py`, `check_windows_wrappers.py`.
- `check_scheduler_runtime.py`: actual scheduled BAT execution in a temporary fake project, forwarding all arguments and returning the fake child's exit code 17. No downloader runs.
- PowerShell parser check of `register_realty_task.ps1`, without executing registration.

Before/after production status SHA-256 for the isolated checks:
`94bc2a2a3a694bd816ae39a757a26ede4029e5036eb474406ee7afbc5f0845ac`.

This unchanged hash describes the test boundary; a separately authorized real update may subsequently replace production status.

## Authorized production run on 28 September 2026

After independent review and a successful full validate, the monitoring-only
command above ran with `TDM_DISABLED=1`, `--no-notify`, `--no-archive` and zero
retries. Run `20260928_135657_544690_297d164e` completed with exit 0 and
`status: success`, from 13:56:57 to 13:58:26 Moscow time (88.6 seconds).
The canonical log is `logs/update_20260928_135657_544690_297d164e.log`.
`failures` and `status_write_errors` are empty. Strict status now passes with
zero warnings, and the strict eight-mart manifest check passes. The three
legacy status warnings were resolved by the actual run, not a test fixture.
The original status is retained in `outputs/audit/runtime_before_live_20260928.json`
with the historical SHA-256 above.

The downloaded `monitoring_2_0_20260928.xlsx` passed prepublication validation.
Processed contains 124,428 unique source-metric pairs; all 25,433 metrics from
the newest workbook independently match its numeric source cells. The current
mart has 7,758 RV and 5,116 OKS rows, both exactly equal to explicit latest-raw
loading. The other 14 processed pickles and seven unrelated manifest entries
remain unchanged. See [monitoring recovery](monitoring_restore.md) for backups,
the component recovery and the separate NBSP area correction.

This is one manual production run. No scheduled task has been registered;
deployment time and logon mode await the user's answer. No ERZ authenticated
run occurred because `config/erzrf.json` is absent. The overall audit remains
incomplete.
