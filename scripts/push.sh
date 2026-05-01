#!/bin/bash
# Push current branch to GitHub.
# Usage: bash scripts/push.sh [branch]   (defaults to current branch)
set -e
REPO="$(cd "$(dirname "$0")/.." && pwd)"
cd "$REPO"

BRANCH="${1:-$(git --no-optional-locks branch --show-current)}"

echo "Pushing $BRANCH → origin"
git --no-optional-locks push origin "$BRANCH"
echo "Pushed: $BRANCH"
