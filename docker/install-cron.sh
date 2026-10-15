#!/usr/bin/env bash
# Install the operator's daily job in the host's existing timezone.
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."
project_root="$(pwd -P)"
schedule_time="${1:-06:00}"
[[ "$schedule_time" =~ ^([01][0-9]|2[0-3]):([0-5][0-9])$ ]] || {
  echo 'Usage: bash docker/install-cron.sh [HH:MM], default 06:00 in server local time' >&2
  exit 1
}
for executable in crontab mktemp awk flock; do command -v "$executable" >/dev/null; done
[[ "$project_root" != *$'\n'* && "$project_root" != *'%'* ]] || {
  echo 'Project path must not contain newlines or cron percent characters' >&2; exit 1;
}
mkdir -p logs state
exec 9>state/.cron-install.lock
flock -n 9 || { echo 'Another cron installation is running' >&2; exit 1; }
temporary="$(mktemp -d logs/.cron-install.XXXXXXXX)"
trap 'rm -f -- "$temporary/before" "$temporary/error" "$temporary/after"; rmdir -- "$temporary"' EXIT
if LC_ALL=C crontab -l >"$temporary/before" 2>"$temporary/error"; then
  :
elif grep -q '^no crontab for ' "$temporary/error"; then
  : >"$temporary/before"
else
  echo 'Cannot read current crontab; no schedule changed' >&2
  cat "$temporary/error" >&2
  exit 1
fi
mkdir -p state/cron-backups
chmod 700 state/cron-backups
backup="state/cron-backups/crontab-before-$(date +%Y%m%dT%H%M%S)-$$.txt"
(umask 077; cp -- "$temporary/before" "$backup"; chmod 600 "$backup")
hour=$((10#${schedule_time%:*}))
minute=$((10#${schedule_time#*:}))
printf -v script '%q' "$project_root/docker/update.sh"
printf -v output '%q' "$project_root/logs/docker-update-cron.log"
job="$minute $hour * * * UPDATE_ERZ=1 /bin/bash $script >> $output 2>&1"
awk -v script="$project_root/docker/update.sh" '
  $0 == "# parser-dashboard daily update" { skip = 1; next }
  skip { skip = 0; next }
  index($0, script) == 0 { print }
' "$temporary/before" >"$temporary/after"
printf '%s\n%s\n' '# parser-dashboard daily update' "$job" >>"$temporary/after"
crontab "$temporary/after"
printf 'Installed for user %s, daily at %s in the existing server timezone.\n' "$(id -un)" "$schedule_time"
date '+Server time: %Y-%m-%d %H:%M:%S %Z'
printf '%s\nBackup: %s\n' "$job" "$backup"
