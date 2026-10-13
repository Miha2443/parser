#!/usr/bin/env bash
# Host lock spans publication, proof of the served release and acknowledgement.
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."
test -f .env || { echo '.env is required' >&2; exit 1; }
mode="${1:-update}"
[[ "$mode" == update || "$mode" == --publish-existing || "$mode" == --recover ]] || {
  echo 'Usage: bash docker/update.sh [--publish-existing|--recover]' >&2; exit 1;
}
mkdir -p state logs runtime/releases
exec 9>state/.docker-update.lock
flock -n 9 || { echo 'Another Docker update is running.' >&2; exit 1; }
host_log="logs/docker-update_$(date -u +%Y%m%dT%H%M%S)_$$.log"
exec > >(tee -a "$host_log") 2>&1

event() { printf '%s step=%s %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$1" "${2:-}"; }
backend_id() { timeout 15 docker compose ps -aq backend; }
backend_signature() {
  local id
  id="$(backend_id)" || return
  [[ -n "$id" ]] || return 0
  timeout 15 docker inspect -f '{{.Id}}|{{.State.Status}}|{{.State.StartedAt}}' "$id"
}
backend_state() {
  local id details
  id="$(backend_id)" || { event backend_state 'inspect=unavailable'; return; }
  if [[ -z "$id" ]]; then event backend_state 'container=absent'; return; fi
  details="$(timeout 15 docker inspect -f '{"id":{{json .Id}},"image":{{json .Config.Image}},"status":{{json .State.Status}},"startedAt":{{json .State.StartedAt}},"finishedAt":{{json .State.FinishedAt}},"exitCode":{{.State.ExitCode}},"oomKilled":{{.State.OOMKilled}},"error":{{json .State.Error}},"restartCount":{{.RestartCount}}}' "$id")" || return
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
      timeout 60 docker compose run --rm --no-deps updater python -m docker.publication "$1" ;;
    *) docker compose run --rm --no-deps updater python -m docker.publication "$1" ;;
  esac
}
ready_once() {
  local target="$1" bound="$2"
  local scope="${3:-full}"
  event readiness "expected=/runtime/$target scope=$scope bound=${bound}s"
  if [[ "$scope" == identity ]]; then
    # The existing backend may still contain an older readiness CLI. Execute
    # host-owned stdlib code so migration does not depend on that image's files.
    timeout "$bound" docker compose exec -T backend python -c '
import json, sys
from urllib.request import urlopen
with urlopen("http://127.0.0.1:8000/api/v1/health", timeout=15) as response:
    health = json.load(response)
if health.get("status") != "ok" or health.get("release") != sys.argv[1]:
    raise SystemExit("Backend does not serve the expected healthy pinned release")
' "/runtime/$target"
  else
    timeout "$bound" docker compose exec -T backend python docker/readiness.py "/runtime/$target"
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
  timeout 120 docker compose up -d --no-deps backend || return
  after="$(backend_signature)" || return
  if [[ -n "$before" && "$before" == "$after" && "$before" == *'|running|'* ]]; then
    event backend_restart 'same container and StartedAt; pin the new release once'
    timeout 60 docker compose restart backend || return
  fi
  backend_state
  ready_target "$(readlink runtime/live)" "${1:-full}"
}

recover() {
  local info previous next live id
  event recovery 'validate journal and live before mutation'
  info="$(control recovery-info)" || {
    event recovery_blocked 'Unknown journal/live combination; keep all data and journal. Inspect runtime/pending.json and this host log.'
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
if [[ "$mode" == update && -n "$before_update" ]]; then
  if [[ ! -L runtime/live ]] || ! ready_once "$(readlink runtime/live)" 20 identity; then
    event preflight_blocked 'Existing backend does not prove a ready immutable live release; no download/restart/stop performed. Complete --publish-existing migration or restore backend readiness first.'
    exit 1
  fi
fi
event updater_start "mode=$mode"
if [[ "$mode" == --publish-existing ]]; then
  run_updater() { control publish; }
else
  run_updater() { docker compose run --rm --no-deps updater; }
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
docker compose up -d --no-deps web
# Archival errors cannot roll back a healthy acknowledged site.
if ! control archive; then event archive_warning 'Healthy site retained; old releases remain available'; fi
event complete "live=$(readlink runtime/live)"
