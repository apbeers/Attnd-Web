#!/bin/bash
# Run all Django tests (unit + integration).
# Usage: bash scripts/test.sh [unit|integration|all]
# Output is saved to scripts/last_test_run.txt for the agent to read back.
set -e
REPO="$(cd "$(dirname "$0")/.." && pwd)"
cd "$REPO"
[ -f "$REPO/venv/bin/activate" ] && source "$REPO/venv/bin/activate"

SUITE="${1:-all}"
LOG="$REPO/scripts/last_test_run.txt"
echo "Running $SUITE tests at $(date)" | tee "$LOG"
echo "======================================" | tee -a "$LOG"

case "$SUITE" in
  unit)
    python manage.py test events.tests --verbosity=2 2>&1 | tee -a "$LOG"
    ;;
  integration)
    python manage.py test events.tests_integration --verbosity=2 2>&1 | tee -a "$LOG"
    ;;
  *)
    python manage.py test events --verbosity=2 2>&1 | tee -a "$LOG"
    ;;
esac

echo "" | tee -a "$LOG"
echo "Done. Full log at scripts/last_test_run.txt"
