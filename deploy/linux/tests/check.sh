#!/usr/bin/env bash
# Runs only in the disposable Debian integration-test container.
set -euo pipefail
[[ "${PARSER_DEBIAN_SMOKE:-}" == 1 && -f /.dockerenv && "$(cat /proc/1/comm)" == systemd ]] || {
  echo 'This check requires the isolated Debian systemd test container.' >&2; exit 1;
}
cd /opt/parser-dashboard
test "$(. /etc/os-release; echo "$VERSION_ID")" = 12
for script in deploy/linux/*.sh; do bash -n "$script"; done
runuser -u parserdash -- .venv/bin/python scripts/update_realty.py all --plan
runuser -u parserdash -- .venv/bin/python deploy/linux/tests/smoke.py unit
runuser -u parserdash -- .venv/bin/python scripts/check_update_realty_runtime.py
runuser -u parserdash -- .venv/bin/python scripts/check_data_access_core.py
runuser -u parserdash -- bash -c 'cd frontend && pnpm test'
runuser -u parserdash -- .venv/bin/python deploy/linux/tests/smoke.py browser
# Stop the schedule during synthetic tests; its calendar is checked separately.
bash deploy/linux/install-services.sh
systemctl stop parser-update.timer
systemd-analyze verify /etc/systemd/system/parser-{api,update}.service /etc/systemd/system/parser-update.timer
systemd-analyze calendar '*-*-* 06:00:00 Europe/Moscow'
runuser -u parserdash -- .venv/bin/python deploy/linux/tests/smoke.py seed
runuser -u parserdash -- .venv/bin/python -m pipeline.build_realty_marts --strict
runuser -u parserdash -- bash deploy/linux/publish-data.sh
systemctl restart parser-api.service
runuser -u parserdash -- .venv/bin/python deploy/linux/tests/smoke.py api
# Exercise publication/restart through the real unit with a fixture updater.
mkdir -p /etc/systemd/system/parser-update.service.d
cat > /etc/systemd/system/parser-update.service.d/smoke.conf <<'EOF'
[Service]
Environment=PARSER_DEBIAN_SMOKE=1
ExecStart=
ExecStart=/opt/parser-dashboard/.venv/bin/python /opt/parser-dashboard/deploy/linux/tests/smoke.py update
EOF
systemctl daemon-reload
.venv/bin/python deploy/linux/tests/smoke.py refresh
.venv/bin/python deploy/linux/tests/smoke.py publication_failure
previous_live="$(readlink -f live)"
previous_pid="$(systemctl show parser-api.service --property=MainPID --value)"
cat > /etc/systemd/system/parser-update.service.d/zz-fail.conf <<'EOF'
[Service]
ExecStart=
ExecStart=/bin/false
EOF
systemctl daemon-reload
if systemctl start parser-update.service; then
  echo 'A failed updater unexpectedly succeeded.' >&2; exit 1
fi
[[ "$(readlink -f live)" == "$previous_live" ]]
[[ "$(systemctl show parser-api.service --property=MainPID --value)" == "$previous_pid" ]]
curl -f http://127.0.0.1:8080/api/v1/catalog >/dev/null
systemctl is-enabled parser-api.service parser-update.timer nginx
echo 'Debian 12 installation, browser, API, publication and systemd refresh checks passed.'
