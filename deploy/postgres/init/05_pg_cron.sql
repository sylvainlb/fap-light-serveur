-- ============================================================================
-- FAP-light — 05_pg_cron.sql : materializers in-database (spec §C1.5)
--   - silver watermark : toutes les 10 min (mêmes watermarks que la full)
--   - gold full refresh : toutes les 10 min (REFRESH MATERIALIZED VIEW)
-- L'extension pg_cron est préchargée via shared_preload_libraries (compose).
-- ============================================================================

CREATE EXTENSION IF NOT EXISTS pg_cron;

-- pg_cron ne tourne que dans la base pg_cron.database_name (compose : fap)
SELECT cron.schedule('fap-light-silver-watermark', '*/10 * * * *',
                     'SELECT public.silver_tick()');
SELECT cron.schedule('fap-light-gold-refresh', '*/10 * * * *',
                     'SELECT public.gold_refresh()');
