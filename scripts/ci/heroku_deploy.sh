#!/usr/bin/env bash
# Deploy the checked-out commit to a Heroku app over git, then wait for /health/.
#   HEROKU_API_KEY  (secret)  HEROKU_APP  (app name)  HEALTH_URL (optional; set it: the app's herokuapp.com
#   hostname serves only Studio sites)
set -euo pipefail
: "${HEROKU_API_KEY:?HEROKU_API_KEY secret is not set}"
: "${HEROKU_APP:?HEROKU_APP variable is not set for this environment}"
if [ -z "${HEALTH_URL:-}" ]; then
  # Newer Heroku apps get a randomized hostname, so ask the API for the real one.
  web_url=$(curl -fsS -H "Authorization: Bearer $HEROKU_API_KEY" \
    -H "Accept: application/vnd.heroku+json; version=3" "https://api.heroku.com/apps/$HEROKU_APP" \
    | python3 -c 'import json,sys; print(json.load(sys.stdin)["web_url"])')
  HEALTH_URL="${web_url%/}/health/"
fi

echo "Deploying $(git rev-parse --short HEAD) to $HEROKU_APP"
# The release phase (Procfile) runs migrations; a failed release fails this push.
git push --force "https://heroku:${HEROKU_API_KEY}@git.heroku.com/${HEROKU_APP}.git" HEAD:refs/heads/main

for attempt in $(seq 1 30); do
  code=$(curl -s -o /dev/null -w '%{http_code}' "$HEALTH_URL" || true)
  if [ "$code" = "200" ]; then
    echo "Healthy: $HEALTH_URL"
    exit 0
  fi
  echo "Waiting for $HEALTH_URL (HTTP $code, attempt $attempt)"
  sleep 10
done
echo "Deploy finished but $HEALTH_URL never returned 200" >&2
exit 1
