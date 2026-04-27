#!/bin/bash
# Push current branch to GitHub origin. Run this on your Mac after the agent commits.
cd "$(dirname "$0")/.."
BRANCH=$(git rev-parse --abbrev-ref HEAD)
echo "Pushing $BRANCH to origin..."
git config user.email "apbeers@icloud.com"
git config user.name "Andrew"
git push origin "$BRANCH"
echo ""
echo "Done. Branch '$BRANCH' pushed to GitHub."
echo "Press any key to close."
read -n 1
