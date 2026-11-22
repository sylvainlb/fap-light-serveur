#!/usr/bin/env bash
# ============================================================================
# FAP-light backup (mandatory: SPOF assumed, spec §C1.1/§5):
#   - full (logical) pg_dump, timestamped, into backups/
#   - continuous WAL archiving must be enabled on the PostgreSQL side in
#     production (wal_level=replica + archive_command), not configured here
#     (local docker).
# ============================================================================
set -Eeuo pipefail
cd "$(dirname "$0")/.."
set -a; [ -f .env ] && . ./.env; set +a

STAMP=$(date -u +%Y%m%dT%H%M%SZ)
OUT="backups/fap-light-${STAMP}.sql.gz"
mkdir -p backups

docker compose exec -T postgres pg_dump -U admin -d fap --no-owner | gzip > "$OUT"
echo "[backup] dump written: $OUT ($(du -h "$OUT" | cut -f1))"
echo "[backup] retention: keep the last 30 dumps (example):"
echo "           ls -1t backups/*.sql.gz | tail -n +31 | xargs -r rm"
