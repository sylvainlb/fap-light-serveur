#!/bin/bash
# FAP-light database init: runs the SQL scripts in order with the role
# passwords passed as psql variables (from the container env).
set -Eeuo pipefail

PSQL=(psql -v ON_ERROR_STOP=1 -U "${POSTGRES_USER:-admin}"
  -v admin_pw="${POSTGRES_PASSWORD:?POSTGRES_PASSWORD required}"
  -v gate_pw="${PG_GATE_PASSWORD:?PG_GATE_PASSWORD required}"
  -v reader_pw="${PG_READER_PASSWORD:?PG_READER_PASSWORD required}"
  -v app_pw="${PG_APP_PASSWORD:?PG_APP_PASSWORD required}"
  -v dsp_agreement_id="${DSP_AGREEMENT_ID:-agreement:tck:ca-s4}"
  -d "${POSTGRES_DB:-fap}")

# The .sql files live in sql/ (subdirectory not scanned by the entrypoint, which
# would run the top-level .sql files without our -v variables).
DIR="$(dirname "$0")"
for f in "$DIR"/sql/0[1-4]*.sql; do
  echo "[init] $(basename "$f")"
  "${PSQL[@]}" -f "$f"
done
# 05_pg_cron.sql: attempted here without failing the init (the temporary init
# server may not have pg_cron.database_name); it is reapplied at each
# startup by the one-shot init-pg-cron service (idempotent).
if [ -f "$DIR/sql/05_pg_cron.sql" ]; then
  echo "[init] 05_pg_cron.sql (attempt, non-fatal)"
  "${PSQL[@]}" -f "$DIR/sql/05_pg_cron.sql" || echo "[init] pg_cron deferred to startup (init-pg-cron)"
fi
echo "[init] OK"
