#!/bin/bash
# Load the sample_data fixture into the database.
# Usage: bash scripts/load_fixture.sh
# WARNING: This replaces existing fixture data. Safe for dev/staging, not prod.
set -e
REPO="$(cd "$(dirname "$0")/.." && pwd)"
cd "$REPO"
[ -f "$REPO/venv/bin/activate" ] && source "$REPO/venv/bin/activate"
python manage.py loaddata events/fixtures/sample_data.json
echo "Fixture loaded: 3 events, 30 locations, notifications."
