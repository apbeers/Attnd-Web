#!/bin/bash
# Codex release helper: run tests, bump build number, commit, and push.
# Usage: bash scripts/codex_release.sh "commit message" [branch]
set -e
REPO="$(cd "$(dirname "$0")/.." && pwd)"
cd "$REPO"

if [ -z "$1" ]; then
  echo "Usage: bash scripts/codex_release.sh \"commit message\" [branch]"
  exit 1
fi

uv sync
uv run python manage.py test
bash "$REPO/scripts/commit_and_push.sh" "$1" "${2:-}"
