#!/usr/bin/env bash
# Host lock spans publication, proof of the served release and acknowledgement.
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."
test -f .env || { echo '.env is required' >&2; exit 1; }
mode="${1:-update}"
[[ "$mode" == update || "$mode" == --publish-existing || "$mode" == --construction-history || "$mode" == --upgrade-code || "$mode" == --recover ]] || {
  echo 'Usage: bash docker/update.sh [--publish-existing|--construction-history|--upgrade-code|--recover]' >&2; exit 1;
}
mkdir -p state logs runtime/releases
exec 9>state/.docker-update.lock
flock -n 9 || { echo 'Another Docker update is running.' >&2; exit 1; }
host_log="logs/docker-update_$(date -u +%Y%m%dT%H%M%S)_$$.log"
exec > >(tee -a "$host_log") 2>&1

event() { printf '%s step=%s %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$1" "${2:-}"; }
# Docker clients may ignore TERM while attached; always bound the final kill too.
bounded() { timeout --kill-after=5s "$@"; }
backend_id() { bounded 15 docker compose ps -aq backend; }
backend_signature() {
  local id
  id="$(backend_id)" || return
  [[ -n "$id" ]] || return 0
  bounded 15 docker inspect -f '{{.Id}}|{{.State.Status}}|{{.State.StartedAt}}' "$id"
}
backend_state() {
  local id details
  id="$(backend_id)" || { event backend_state 'inspect=unavailable'; return; }
  if [[ -z "$id" ]]; then event backend_state 'container=absent'; return; fi
  details="$(bounded 15 docker inspect -f '{"id":{{json .Id}},"image":{{json .Config.Image}},"imageId":{{json .Image}},"status":{{json .State.Status}},"healthStatus":{{if .State.Health}}{{json .State.Health.Status}}{{else}}null{{end}},"startedAt":{{json .State.StartedAt}},"finishedAt":{{json .State.FinishedAt}},"exitCode":{{.State.ExitCode}},"oomKilled":{{.State.OOMKilled}},"error":{{json .State.Error}},"restartCount":{{.RestartCount}}}' "$id")" || return
  event backend_state "$details"
}
finish() {
  local status=$?
  trap - EXIT
  event exit "status=$status log=$host_log"
  backend_state || true
}
trap finish EXIT
event start "mode=$mode log=$host_log"
backend_state

control() {
  case "$1" in
    recovery-info|activate-next|rollback|ack)
      bounded 60 docker compose run --rm --no-deps -T --interactive=false updater python -m docker.publication "$1" </dev/null ;;
    *) docker compose run --rm --no-deps -T --interactive=false updater python -m docker.publication "$1" </dev/null ;;
  esac
}
ready_once() {
  local target="$1" bound="$2"
  local scope="${3:-full}"
  event readiness "expected=/runtime/$target scope=$scope bound=${bound}s"
  if [[ "$scope" == identity ]]; then
    # The existing backend may still contain an older readiness CLI. Execute
    # host-owned stdlib code so migration does not depend on that image's files.
    bounded "$bound" docker compose exec -T --interactive=false backend python -c '
import json, sys
from urllib.request import urlopen
with urlopen("http://127.0.0.1:8000/api/v1/health", timeout=15) as response:
    health = json.load(response)
if health.get("status") != "ok" or health.get("release") != sys.argv[1]:
    raise SystemExit("Backend does not serve the expected healthy pinned release")
' "/runtime/$target" </dev/null
  else
    bounded "$bound" docker compose exec -T --interactive=false backend python docker/readiness.py "/runtime/$target" </dev/null
  fi
}
ready_target() {
  local target="$1" deadline remaining
  deadline=$((SECONDS + ${READINESS_TIMEOUT:-90}))
  while ((SECONDS < deadline)); do
    remaining=$((deadline - SECONDS))
    ((remaining <= 30)) || remaining=30
    if ready_once "$target" "$remaining" "${2:-full}"; then return 0; fi
    sleep 3
  done
  backend_state
  return 1
}

restart_backend() {
  local before after
  before="$(backend_signature)" || return
  event backend_start 'compose up; restart only if existing running container did not start anew'
  bounded 120 docker compose up -d --no-deps backend || return
  after="$(backend_signature)" || return
  if [[ -n "$before" && "$before" == "$after" && "$before" == *'|running|'* ]]; then
    event backend_restart 'same container and StartedAt; pin the new release once'
    bounded 60 docker compose restart backend || return
  fi
  backend_state
  ready_target "$(readlink runtime/live)" "${1:-full}"
}

recover() {
  local info previous next live id status
  event recovery 'validate journal and live before mutation'
  info="$(control recovery-info)" || {
    status=$?
    if [[ "$status" == 124 || "$status" == 137 ]]; then
      event recovery_blocked "reason=control_timeout exitCode=$status Recovery command timed out; journal/live consistency is unknown. Keep all data and journal; inspect Docker and this host log."
    else
      event recovery_blocked "reason=control_failed exitCode=$status Recovery command failed; keep all data and journal. Inspect runtime/pending.json and the command error above."
    fi
    return 1
  }
  read -r previous next live <<< "$info"
  event recovery "previous=$previous next=$next live=$live"
  if ready_once "$next" 20; then
    event recovery 'backend already serves next; acknowledge without restart'
    control activate-next || return
    control ack || return
    return 0
  fi
  if [[ "$previous" != '-' ]] && ready_once "$previous" 20 identity; then
    event recovery 'backend already serves previous; restore pointer and acknowledge without restart'
    control rollback || return
    control ack || return
    return 0
  fi
  id="$(backend_id)" || return
  if [[ "$previous" == '-' ]]; then
    if [[ -n "$id" ]]; then
      event recovery_blocked 'No previous immutable release and an existing backend cannot prove next readiness. Backend was not stopped; journal retained; no download started. Inspect backend state/logs and run --recover after resolving it.'
      backend_state
      return 1
    fi
    event recovery 'No backend exists; safely start the validated bootstrap candidate'
    control activate-next || return
    restart_backend || return
    control ack || return
    return 0
  fi
  event recovery 'Neither release ready; restore verified previous pointer and start backend'
  control rollback || return
  restart_backend identity || {
    event recovery_blocked 'Previous release is not ready; keep journal. Data rollback does not restore image/configuration.'
    return 1
  }
  control ack || return
}

if [[ -f runtime/pending.json || -L runtime/pending.json ]]; then recover; fi
if [[ "$mode" == --recover ]]; then
  event recovery_complete 'No downloads performed; inspect backend_state above for actual container status'
  exit 0
fi

before_update="$(backend_signature)"
if [[ "$mode" != --publish-existing && -n "$before_update" ]]; then
  if [[ ! -L runtime/live ]]; then
    if [[ -e runtime/live ]]; then
      event preflight_blocked 'reason=live_not_symlink Existing runtime/live is not a published release symlink; no download/restart/stop performed. Inspect its contents and backend mounts before resolving this path conflict. Preserve the existing path: --publish-existing refuses an ordinary directory/file, and --recover without a pending journal does not migrate.'
    else
      event preflight_blocked 'reason=live_missing Published runtime/live symlink is missing; no download/restart/stop performed. Inspect runtime/live and backend mounts. Complete the documented offline ETL/mart rebuild and --publish-existing migration; --recover without a pending journal does not migrate.'
    fi
    exit 1
  fi
  if ! ready_once "$(readlink runtime/live)" 20 identity; then
    event preflight_blocked 'reason=live_identity_not_ready Existing backend does not prove the expected healthy pinned runtime/live release; no download/restart/stop performed. Inspect /api/v1/health, backend_state, mounts and backend logs. Restore readiness before collection; inspect runtime/pending.json before using --recover.'
    exit 1
  fi
fi
if [[ "$mode" == --upgrade-code ]]; then
  [[ -L runtime/live ]] || { event code_upgrade_blocked 'A published runtime/live release is required; use the bootstrap procedure for a new installation'; exit 1; }
  # Keep previous application images addressable; data recovery does not
  # restore an older image after compose replaces a container.
  backup_id="$(date -u +%Y%m%dT%H%M%S)-$$"
  for service in backend web; do
    id="$(bounded 15 docker compose ps -aq "$service")"
    if [[ -n "$id" ]]; then
      old_image="$(bounded 15 docker inspect -f '{{.Image}}' "$id")"
      backup_image="parser-dashboard-previous-$service:$backup_id"
      bounded 30 docker image tag "$old_image" "$backup_image"
      event code_image_preserved "service=$service image=$backup_image"
    fi
  done
  bounded 1800 docker compose --profile updater build
  if ! restart_backend; then
    event code_upgrade_failed 'New backend compatibility was not proven; web was not updated. Previous image tags are logged above. Data pointer was not changed; application-image rollback is separate.'
    exit 1
  fi
  bounded 120 docker compose up -d --no-deps web
  event code_upgrade_complete "live=$(readlink runtime/live) downloads=none data_publication=none"
  exit 0
fi

event updater_start "mode=$mode"
if [[ "$mode" == --publish-existing ]]; then
  run_updater() { control publish; }
elif [[ "$mode" == --construction-history ]]; then
  run_updater() {
    event construction_history 'collect missing months from 2020; publication requires collector success'
    docker compose run --rm --no-deps -T --interactive=false updater python -u scripts/update_realty.py construction --retries 0 --collect-only --require-collected </dev/null || return
    control publish
  }
else
  run_updater() { docker compose run --rm --no-deps -T --interactive=false updater </dev/null; }
fi
if ! run_updater; then
  if [[ -f runtime/pending.json || -L runtime/pending.json ]]; then
    recover || exit 1
  elif [[ "$(backend_signature)" == "$before_update" ]]; then
    event updater_failed 'No publication; backend ID and StartedAt unchanged; no restart/stop performed'
  else
    event updater_failed 'No publication; backend state changed independently; inspect state, no cause assumed'
  fi
  exit 1
fi

event publication_complete "live=$(readlink runtime/live)"
if ! restart_backend; then
  event readiness_failed 'New release not ready; perform bounded safe recovery'
  recover || exit 1
  exit 1
fi
control ack
bounded 120 docker compose up -d --no-deps web
# Archival errors cannot roll back a healthy acknowledged site.
if ! control archive; then event archive_warning 'Healthy site retained; old releases remain available'; fi
event complete "live=$(readlink runtime/live)"
