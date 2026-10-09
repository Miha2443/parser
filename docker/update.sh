#!/usr/bin/env bash
# Cron and interactive runs use the same lock, publication, restart and recovery.
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."
test -f .env || { echo '.env is required' >&2; exit 1; }
mode="${1:-update}"
[[ "$mode" == update || "$mode" == --publish-existing ]] || {
  echo 'Usage: bash docker/update.sh [--publish-existing]' >&2; exit 1;
}
mkdir -p state logs runtime/releases
exec 9>state/.docker-update.lock
flock -n 9 || { echo 'Another Docker update is running.' >&2; exit 1; }

control() {
  docker compose run --rm --no-deps updater python -m docker.publication "$1"
}

ready() {
  local target deadline remaining
  target="$(readlink runtime/live)"
  [[ "$target" == releases/* && -d "runtime/$target" ]] || return 1
  deadline=$((SECONDS + ${READINESS_TIMEOUT:-90}))
  while ((SECONDS < deadline)); do
    remaining=$((deadline - SECONDS))
    ((remaining <= 30)) || remaining=30
    if timeout "$remaining" docker compose exec -T backend python docker/readiness.py "/runtime/$target"; then
      return 0
    fi
    sleep 3
  done
  return 1
}

restart_backend() {
  # up handles first startup and an image/container replacement as well as restart.
  docker compose up -d --no-deps backend || return
  docker compose restart backend || return
  ready
}

restore() {
  control rollback || return
  if [[ -L runtime/live ]]; then
    restart_backend || { echo 'Previous release also failed readiness; recovery journal retained.' >&2; return 1; }
  else
    docker compose stop backend || return
  fi
  control ack || return
}

if [[ -f runtime/pending.json ]]; then
  echo 'Recovering an interrupted publication before the next update.'
  restore
fi

run_updater() {
  if [[ "$mode" == --publish-existing ]]; then
    control publish
  else
    docker compose run --rm --no-deps updater
  fi
}

if ! run_updater; then
  [[ ! -f runtime/pending.json ]] || restore
  echo 'Updater failed; previous release retained.' >&2
  exit 1
fi

if ! restart_backend; then
  echo 'New release failed readiness; restoring previous release.' >&2
  restore
  exit 1
fi
control ack
docker compose up -d --no-deps web
echo "Update complete: $(readlink runtime/live)"
