#!/bin/bash
# Init de la base FAP-light : exécute les scripts SQL dans l'ordre avec les
# mots de passe des rôles passés en variables psql (depuis l'env du conteneur).
set -Eeuo pipefail

PSQL=(psql -v ON_ERROR_STOP=1 -U "${POSTGRES_USER:-admin}"
  -v admin_pw="${POSTGRES_PASSWORD:?POSTGRES_PASSWORD requis}"
  -v gate_pw="${PG_GATE_PASSWORD:?PG_GATE_PASSWORD requis}"
  -v reader_pw="${PG_READER_PASSWORD:?PG_READER_PASSWORD requis}"
  -v app_pw="${PG_APP_PASSWORD:?PG_APP_PASSWORD requis}"
  -v dsp_agreement_id="${DSP_AGREEMENT_ID:-agreement:tck:ca-s4}"
  -d "${POSTGRES_DB:-fap}")

# Les .sql vivent dans sql/ (sous-dossier non scanné par l'entrypoint, qui
# exécutérait les .sql du premier niveau sans nos variables -v).
DIR="$(dirname "$0")"
for f in "$DIR"/sql/0[1-4]*.sql; do
  echo "[init] $(basename "$f")"
  "${PSQL[@]}" -f "$f"
done
# 05_pg_cron.sql : tenté ici sans faire échouer l'init (le serveur temporaire
# d'init peut ne pas avoir pg_cron.database_name) ; il est réappliqué à chaque
# démarrage par le service one-shot init-pg-cron (idempotent).
if [ -f "$DIR/sql/05_pg_cron.sql" ]; then
  echo "[init] 05_pg_cron.sql (tentative, non fatale)"
  "${PSQL[@]}" -f "$DIR/sql/05_pg_cron.sql" || echo "[init] pg_cron différé au démarrage (init-pg-cron)"
fi
echo "[init] OK"
