#!/bin/bash
# Start the local Django dev server at http://127.0.0.1:8000
# Loads environment variables from .env if present.
# Usage: double-click, or: bash scripts/runserver.command
cd "$(dirname "$0")/.."

if [ -f .env ]; then
  set -a
  source .env
  set +a
  echo "Loaded .env"
fi

echo "Starting dev server at http://127.0.0.1:8000 ..."
uv run python manage.py runserver
