-- ============================================================================
-- FAP-light — 05_pg_cron.sql : in-database materializers (spec §C1.5)
--   - silver watermark: every 10 min (same watermarks as the full)
--   - gold full refresh: every 10 min (REFRESH MATERIALIZED VIEW)
-- Idempotent. Applied AFTER the first server startup (the temporary init
-- server does not have pg_cron.database_name=fap yet): via the one-shot
-- `init-pg-cron` service (compose) or `docker exec … psql -f 05_pg_cron.sql`.
-- The server runs with shared_preload_libraries=pg_cron (compose command).
-- ============================================================================

CREATE EXTENSION IF NOT EXISTS pg_cron;

DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM cron.job WHERE jobname = 'fap-light-silver-watermark') THEN
    PERFORM cron.schedule('fap-light-silver-watermark', '*/10 * * * *',
                          'SELECT public.silver_tick()');
  END IF;
  IF NOT EXISTS (SELECT 1 FROM cron.job WHERE jobname = 'fap-light-gold-refresh') THEN
    PERFORM cron.schedule('fap-light-gold-refresh', '*/10 * * * *',
                          'SELECT public.gold_refresh()');
  END IF;
END $$;
