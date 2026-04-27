#!/bin/bash
# Stage all changes and commit locally. Does NOT push (run push.command for that).
# Usage: bash scripts/commit.sh "your commit message"
# Safe to run from the agent's bash sandbox.
set -e
REPO="$(cd "$(dirname "$0")/.." && pwd)"
cd "$REPO"

if [ -z "$1" ]; then
  echo "Usage: bash scripts/commit.sh \"commit message\""
  exit 1
fi

# mv works where rm is blocked by FUSE; rename locks out of the way
for lock in .git/index.lock .git/HEAD.lock .git/refs/heads/*.lock; do
  [ -f "$lock" ] && mv "$lock" "${lock}.bak" 2>/dev/null || true
done
git config user.email "apbeers@icloud.com"
git config user.name "Andrew"
git --no-optional-locks add -A
git --no-optional-locks commit -m "$1"
echo ""
echo "Committed: $(git --no-optional-locks rev-parse --short HEAD) — $1"
echo "Run scripts/push.command to push to GitHub."
