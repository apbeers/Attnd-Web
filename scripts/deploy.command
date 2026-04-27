#!/bin/bash
# Deploy to Heroku by pushing main to the heroku remote.
# Heroku runs: python manage.py migrate && collectstatic, then starts gunicorn.
cd "$(dirname "$0")/.."
BRANCH=$(git rev-parse --abbrev-ref HEAD)
if [ "$BRANCH" != "main" ]; then
  echo "Warning: you are on '$BRANCH', not main."
  echo -n "Deploy anyway? (y/N): "
  read -r answer
  [ "$answer" != "y" ] && exit 0
fi
echo "Deploying to Heroku..."
git push heroku "$BRANCH:main"
echo ""
echo "Deploy complete. Check logs with: heroku logs --tail"
echo "Press any key to close."
read -n 1
