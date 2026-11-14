#!/usr/bin/env bash
# Local PostgreSQL 18 (what Heroku and CI run) in Docker, at the address settings.py uses by default:
#   postgres://attnd:attnd@localhost:5432/attnd
# Usage: scripts/dev_db.sh          start it (creates it the first time; data persists in a volume)
#        scripts/dev_db.sh stop     stop it
#        scripts/dev_db.sh reset    delete it and its data
set -euo pipefail
NAME=attnd-pg
IMAGE=postgres:18

case "${1:-start}" in
  stop)  docker stop "$NAME" >/dev/null && echo "Stopped $NAME"; exit 0 ;;
  reset) docker rm -f "$NAME" >/dev/null 2>&1 || true; docker volume rm -f attnd-pg-data >/dev/null; echo "Deleted $NAME"; exit 0 ;;
esac

if ! docker info >/dev/null 2>&1; then
  echo "Docker isn't running. Start Docker Desktop (Mac) or 'dockerd &' (Linux), then run this again." >&2
  exit 1
fi
if [ -z "$(docker ps -aq -f name=^${NAME}$)" ]; then
  docker run -d --name "$NAME" -e POSTGRES_USER=attnd -e POSTGRES_PASSWORD=attnd -e POSTGRES_DB=attnd \
    -p 5432:5432 -v attnd-pg-data:/var/lib/postgresql "$IMAGE" >/dev/null
else
  docker start "$NAME" >/dev/null
fi
for _ in $(seq 1 30); do
  if docker exec "$NAME" pg_isready -U attnd >/dev/null 2>&1; then
    echo "Postgres is ready: postgres://attnd:attnd@localhost:5432/attnd ($(docker exec "$NAME" postgres --version))"
    exit 0
  fi
  sleep 1
done
echo "Postgres didn't become ready; see: docker logs $NAME" >&2
exit 1
