# PostgreSQL 16 + pg_cron (scheduler in-database des materializers, spec §C1.5)
FROM postgres:16

RUN apt-get update \
    && apt-get install -y --no-install-recommends postgresql-16-cron \
    && rm -rf /var/lib/apt/lists/*

# pg_cron doit être préchargé (shared_preload_libraries) — passé via la commande
# du service dans docker-compose.yml :
#   postgres -c shared_preload_libraries=pg_cron -c pg_cron.database_name=fap
