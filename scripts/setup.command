#!/bin/bash
# Set up the local dev environment: install packages and collect static files.
# Run this once on a new machine, and again after adding/changing dependencies.
# Usage: double-click, or: bash scripts/setup.command
set -e
cd "$(dirname "$0")/.."

echo "=== Installing packages with uv ==="
uv sync

echo ""
echo "=== Collecting static files ==="
uv run python manage.py collectstatic --noinput

echo ""
echo "✅ Setup complete. Run scripts/runserver.command to start the dev server."
echo "Press any key to close."
read -n 1
