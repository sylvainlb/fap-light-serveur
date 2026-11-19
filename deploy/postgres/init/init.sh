#!/bin/bash
# Init de la base FAP-light : exécute les scripts SQL dans l'ordre avec les
# mots de passe des rôles passés en variables psql (depuis l'env du conteneur).
set -Eeuo pipefail

PSQL=(psql -v ON_ERROR_STOP=1
  -v admin_pw="${POSTGRES_PASSWORD:?POSTGRES_PASSWORD requis}"
  -v gate_pw="${PG_GATE_PASSWORD:?PG_GATE_PASSWORD requis}"
  -v reader_pw="${PG_READER_PASSWORD:?PG_READER_PASSWORD requis}"
  -v app_pw="${PG_APP_PASSWORD:?PG_APP_PASSWORD requis}"
  -v dsp_agreement_id="${DSP_AGREEMENT_ID:-agreement:tck:ca-s4}"
  -d "${POSTGRES_DB:-fap}")

DIR="$(dirname "$0")"
for f in 01_roles.sql 02_core.sql 03_silver_views.sql 04_gold_matviews.sql 05_pg_cron.sql; do
  echo "[init] $f"
  "${PSQL[@]}" -f "$DIR/$f"
done
echo "[init] OK"
