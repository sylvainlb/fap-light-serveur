#!/usr/bin/env bash
# ============================================================================
# Backup FAP-light (obligatoire : SPOF assumé, spec §C1.1/§5) :
#   - pg_dump complet (logique) horodaté dans backups/
#   - l'archivage WAL continu est à activer côté PostgreSQL en production
#     (wal_level=replica + archive_command), non configuré ici (docker local).
# ============================================================================
set -Eeuo pipefail
cd "$(dirname "$0")/.."
set -a; [ -f .env ] && . ./.env; set +a

STAMP=$(date -u +%Y%m%dT%H%M%SZ)
OUT="backups/fap-light-${STAMP}.sql.gz"
mkdir -p backups

docker compose exec -T postgres pg_dump -U admin -d fap --no-owner | gzip > "$OUT"
echo "[backup] dump écrit: $OUT ($(du -h "$OUT" | cut -f1))"
echo "[backup] rétention : garder les 30 derniers dumps (exemple) :"
echo "           ls -1t backups/*.sql.gz | tail -n +31 | xargs -r rm"
