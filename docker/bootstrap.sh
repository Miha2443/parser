#!/usr/bin/env bash
# Run as the operator whose UID/GID are configured in .env.
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."
test -f .env || { echo 'Copy .env.example to .env and configure OP_UID/OP_GID first.' >&2; exit 1; }
for executable in docker flock timeout; do command -v "$executable" >/dev/null; done
docker compose version >/dev/null
configured_uid="$(sed -n 's/^OP_UID=\([0-9]*\)[[:space:]]*$/\1/p' .env)"
configured_gid="$(sed -n 's/^OP_GID=\([0-9]*\)[[:space:]]*$/\1/p' .env)"
[[ "$configured_uid" == "$(id -u)" && "$configured_gid" == "$(id -g)" ]] || {
  echo 'OP_UID/OP_GID must match the current operator. Run id -u and id -g.' >&2; exit 1;
}
mkdir -p downloads data state logs config runtime/releases
chmod 600 .env
docker compose --profile updater build
timeout --kill-after=5s 120s docker compose run --rm --no-deps -T --interactive=false updater python -m docker.publication seed </dev/null
echo 'Images and working directories ready. Run bash docker/update.sh; ERZ is skipped by default. Configure config/erzrf.json only with UPDATE_ERZ=1.'
