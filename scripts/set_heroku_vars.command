#!/bin/bash
# Set attestation-related Heroku config vars for attnd-web.
set -e
cd "$(dirname "$0")/.."

APP="attnd-web"

echo "==> Setting Heroku config vars on $APP..."
heroku config:set \
  APPLE_APP_BUNDLE_ID="com.arcu.attnd-ios2" \
  APPLE_TEAM_ID="8T487X8VY7" \
  ANDROID_PACKAGE_NAME="com.arcu.attnd" \
  --app "$APP"

echo ""
echo "Done. Press any key to close."
read -n 1
