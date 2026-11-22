# PostgreSQL 16 + pg_cron (in-database scheduler of the materializers, spec §C1.5)
FROM postgres:16

RUN apt-get update \
    && apt-get install -y --no-install-recommends postgresql-16-cron \
    && rm -rf /var/lib/apt/lists/*

# pg_cron must be preloaded (shared_preload_libraries) — passed via the service
# command in docker-compose.yml:
#   postgres -c shared_preload_libraries=pg_cron -c cron.database_name=fap
