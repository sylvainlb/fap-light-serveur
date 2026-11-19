#!/usr/bin/env bash
# Attend que les services du compose soient prêts (postgres healthy + ORCE up).
set -Eeuo pipefail
cd "$(dirname "$0")/.."

for i in $(seq 1 60); do
  pg_ok=$(docker compose ps postgres --format json 2>/dev/null | grep -o '"healthy"' | head -1 || true)
  orce_ok=$(curl -sf -o /dev/null "http://localhost:${ORCE_PORT:-1880}/health" && echo ok || true)
  if [ -n "$pg_ok" ] && [ "$orce_ok" = "ok" ]; then
    echo "[wait] postgres healthy, orce /health ok"
    exit 0
  fi
  sleep 5
done
echo "[wait] TIMEOUT — état des services :" >&2
docker compose ps >&2 || true
exit 1
