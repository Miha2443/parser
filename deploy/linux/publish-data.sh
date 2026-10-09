#!/usr/bin/env bash
# Snapshot the completed working data, validate it, then atomically switch API input.
# Run as parserdash. The old API keeps reading its resolved, immutable release
# until systemd restarts it after this script exits successfully.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT"
if [[ "$ROOT" != /opt/parser-dashboard ]]; then
  echo 'Data publication requires /opt/parser-dashboard' >&2
  exit 1
fi
command -v rsync >/dev/null || { echo 'rsync is required' >&2; exit 1; }
command -v flock >/dev/null || { echo 'flock is required' >&2; exit 1; }
test -x .venv/bin/python || { echo 'Python environment is missing' >&2; exit 1; }

mkdir -p state data-releases
exec 9>state/.publish.lock
flock -n 9 || { echo 'Another data publication is running' >&2; exit 1; }

if [[ -e live && ! -L live ]]; then
  echo 'Refusing to replace a non-symlink live directory' >&2
  exit 1
fi
previous=''
if [[ -L live ]]; then
  previous="$(readlink -f live)"
  [[ "$previous" == "$ROOT/data-releases/"* && -d "$previous" ]] || {
    echo 'live does not point into data-releases' >&2; exit 1;
  }
fi

release="$(mktemp -d "$ROOT/data-releases/.building-XXXXXXXX")"
echo "Preparing data release $release"

copy_tree() {
  local relative="$1"
  local source="$ROOT/$relative/"
  local target="$release/$relative/"
  mkdir -p "$target"
  local options=(-a --delete)
  if [[ -n "$previous" && -d "$previous/$relative" ]]; then
    options+=("--link-dest=$previous/$relative")
  fi
  rsync "${options[@]}" "$source" "$target"
}

copy_tree downloads
copy_tree data/processed
copy_tree data/marts
copy_tree data/derived
copy_tree logs
# Archived raw files are retained in the working tree, not served by the API.
mkdir -p "$release/data/raw/realty"
raw_options=(-a --delete --exclude=_archive/)
if [[ -n "$previous" && -d "$previous/data/raw/realty" ]]; then
  raw_options+=("--link-dest=$previous/data/raw/realty")
fi
rsync "${raw_options[@]}" "$ROOT/data/raw/realty/" "$release/data/raw/realty/"

PARSER_ROOT="$release" PARSER_DOWNLOADS="$release/downloads" \
  .venv/bin/python -m pipeline.build_realty_marts --check --strict
PARSER_ROOT="$release" PARSER_DOWNLOADS="$release/downloads" \
  PARSER_REQUIRE_REALTY_MARTS=1 .venv/bin/python -c \
  'from backend.profile_service import ProfileService; from pipeline.data_access import DataContext; assert ProfileService(DataContext.from_environment()).catalog()["developers"]'

final="$ROOT/data-releases/$(date -u +%Y%m%dT%H%M%SZ)-$$"
[[ ! -e "$final" ]] || { echo 'Release path already exists' >&2; exit 1; }
mv -T -- "$release" "$final"
if [[ -e "$ROOT/.live-next" && ! -L "$ROOT/.live-next" ]]; then
  echo 'Refusing to replace a non-symlink .live-next' >&2
  exit 1
fi
ln -sfn "$final" "$ROOT/.live-next"
mv -Tf "$ROOT/.live-next" "$ROOT/live"
echo "Published $final; the API can now restart against this release."
