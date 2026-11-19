-- ============================================================================
-- FAP-light — 05_pg_cron.sql : materializers in-database (spec §C1.5)
--   - silver watermark : toutes les 10 min (mêmes watermarks que la full)
--   - gold full refresh : toutes les 10 min (REFRESH MATERIALIZED VIEW)
-- Idempotent. Appliqué APRÈS le premier démarrage du serveur (le serveur
-- temporaire d'init n'a pas encore pg_cron.database_name=fap) : via le service
-- one-shot `init-pg-cron` (compose) ou `docker exec … psql -f 05_pg_cron.sql`.
-- Le serveur tourne avec shared_preload_libraries=pg_cron (commande compose).
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
