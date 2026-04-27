#!/bin/bash
# Run pending Django migrations.
# Usage: bash scripts/migrate.sh
set -e
REPO="$(cd "$(dirname "$0")/.." && pwd)"
cd "$REPO"
[ -f "$REPO/venv/bin/activate" ] && source "$REPO/venv/bin/activate"
python manage.py migrate --verbosity=2
