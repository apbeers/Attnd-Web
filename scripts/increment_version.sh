#!/bin/bash
# Increment the build number in version.txt and print it.
set -e
REPO="$(cd "$(dirname "$0")/.." && pwd)"
VERSION_FILE="$REPO/version.txt"

current=$(cat "$VERSION_FILE" | tr -d '[:space:]')
next=$((current + 1))
echo "$next" > "$VERSION_FILE"
echo "Build $next"
