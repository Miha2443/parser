#!/usr/bin/env bash
# Run as the application user, from any directory, after installing OS tools.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT"

command -v python3 >/dev/null || { echo 'python3 is required' >&2; exit 1; }
python3 -c 'import sys; assert (3, 11) <= sys.version_info[:2] < (3, 14), "Python 3.11-3.13 required"'
command -v node >/dev/null || { echo 'Node.js 24 is required to build frontend' >&2; exit 1; }
node -e 'if (Number(process.versions.node.split(".")[0]) !== 24) process.exit(1)' || {
  echo 'Node.js 24 is required to build frontend' >&2; exit 1;
}
command -v pnpm >/dev/null || { echo 'Install pnpm 10 before running setup' >&2; exit 1; }
pnpm --version | grep -Eq '^10\.' || { echo 'pnpm 10 is required' >&2; exit 1; }
if ! command -v chromium >/dev/null && ! command -v google-chrome >/dev/null; then
  echo 'Chromium or Google Chrome is required by the collectors' >&2
  exit 1
fi

python3 -m venv .venv
.venv/bin/python -m pip install --upgrade pip
.venv/bin/python -m pip install -r requirements-linux.txt
mkdir -p downloads data/raw/realty data/processed data/marts/realty data/derived state logs

(
  cd frontend
  pnpm install --frozen-lockfile
  VITE_DATA_MODE=api pnpm build
)

echo 'Environment and frontend are ready.'
echo 'Check the download plan: .venv/bin/python scripts/update_realty.py all --plan'
echo 'Full automated update: .venv/bin/python scripts/update_realty.py all'
