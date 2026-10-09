#!/usr/bin/env bash
# Run with sudo after cloning to /opt/parser-dashboard and running setup.sh.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
if [[ "$(id -u)" -ne 0 ]]; then
  echo 'Run with sudo' >&2
  exit 1
fi
if [[ "$ROOT" != /opt/parser-dashboard ]]; then
  echo 'Clone or copy the project to /opt/parser-dashboard first' >&2
  exit 1
fi
id parserdash >/dev/null 2>&1 || { echo 'Create user parserdash first' >&2; exit 1; }
test -x "$ROOT/.venv/bin/python" || { echo 'Run deploy/linux/setup.sh as parserdash first' >&2; exit 1; }
test -f "$ROOT/frontend/dist/index.html" || { echo 'Frontend build is missing' >&2; exit 1; }
test -x "$ROOT/deploy/linux/publish-data.sh" || { echo 'Data publisher is missing' >&2; exit 1; }

install -m 0644 "$ROOT/deploy/linux/parser-api.service" /etc/systemd/system/parser-api.service
install -m 0644 "$ROOT/deploy/linux/parser-update.service" /etc/systemd/system/parser-update.service
install -m 0644 "$ROOT/deploy/linux/parser-update.timer" /etc/systemd/system/parser-update.timer
install -m 0644 "$ROOT/deploy/linux/nginx-parser.conf" /etc/nginx/sites-available/parser-dashboard.conf
ln -sfn /etc/nginx/sites-available/parser-dashboard.conf /etc/nginx/sites-enabled/parser-dashboard.conf
nginx -t
systemctl daemon-reload
systemctl enable --now parser-api.service parser-update.timer
systemctl restart parser-api.service
systemctl enable --now nginx
systemctl reload nginx

echo 'Services installed. Site: http://127.0.0.1:8080/home (SSH tunnel required from another computer).'
echo 'Start full download now: sudo systemctl start parser-update.service'
