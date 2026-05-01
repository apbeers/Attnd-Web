#!/bin/bash
# Regenerate uv.lock after dependency changes, then commit and push.
set -e
cd "$(dirname "$0")/.."

echo "==> Running uv lock..."
uv lock

echo "==> Committing updated lockfile..."
for lock in .git/index.lock .git/HEAD.lock; do
  [ -f "$lock" ] && mv "$lock" "${lock}.bak" 2>/dev/null || true
done
git config user.email "apbeers@icloud.com"
git config user.name "Andrew"
git --no-optional-locks add uv.lock
git --no-optional-locks commit -m "Update uv.lock for PyJWT, cbor2, google-auth"

echo "==> Pushing to GitHub..."
bash scripts/push.sh

echo ""
echo "Done. Press any key to close."
read -n 1
