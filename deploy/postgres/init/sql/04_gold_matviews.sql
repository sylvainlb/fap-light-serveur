-- ============================================================================
-- FAP-light — 04_gold_matviews.sql : gold matviews (showcase, spec §C1.2),
-- refreshed by pg_cron via gold_refresh() (spec §C1.5, GOLD_TABLE_ORDER order).
-- ----------------------------------------------------------------------------
-- The 6 assets declared in datasets.json (DSP catalog, CA-S3):
--   net_grid_hourly, energy_balance_hourly, anomaly_candidates,
--   event_impact_daily, weather_hourly, streetlight_zone_hourly
-- + 3 matviews consumed by the Superset dashboards (CA-S8):
--   city_dashboard_summary, energy_cost_daily, pv_performance_hourly
-- ============================================================================

-- Phase 1 — base aggregates -------------------------------------------------
CREATE MATERIALIZED VIEW IF NOT EXISTS gold.weather_hourly AS
SELECT
  w.site_id,
  date_trunc('hour', w.event_timestamp)          AS hour,
  avg(w.temperature_c)                            AS temperature_c,
  avg(w.irradiance_wm2)                           AS irradiance_wm2,
  avg(w.humidity_pct)                             AS humidity_pct,
  avg(w.wind_speed_ms)                            AS wind_speed_ms,
  avg(w.cloud_cover_pct)                          AS cloud_cover_pct
FROM silver.weather w
GROUP BY w.site_id, date_trunc('hour', w.event_timestamp);

CREATE MATERIALIZED VIEW IF NOT EXISTS gold.pv_performance_hourly AS
SELECT
  p.site_id,
  date_trunc('hour', p.event_timestamp)          AS hour,
  sum(p.power_kw)                                 AS pv_generation_kw,
  avg(p.capacity_factor)                          AS capacity_factor
FROM silver.pv_generation p
GROUP BY p.site_id, date_trunc('hour', p.event_timestamp);

CREATE MATERIALIZED VIEW IF NOT EXISTS gold.streetlight_zone_hourly AS
SELECT
  s.zone_id,
  date_trunc('hour', s.event_timestamp)          AS hour,
  avg(s.dim_level)                                AS avg_dim_level,
  sum(s.power_kw)                                 AS total_power_kw
FROM silver.streetlight s
GROUP BY s.zone_id, date_trunc('hour', s.event_timestamp);

CREATE MATERIALIZED VIEW IF NOT EXISTS gold.event_impact_daily AS
SELECT
  e.zone_id,
  (e.event_timestamp::date)                       AS event_date,
  count(*)                                        AS event_count,
  avg(e.severity)                                 AS avg_severity
FROM silver.city_event e
GROUP BY e.zone_id, (e.event_timestamp::date);

-- Phase 2 — hourly energy (depends on silver.energy_meter + energy_price) ---
CREATE MATERIALIZED VIEW IF NOT EXISTS gold.net_grid_hourly AS
SELECT
  m.site_id,
  date_trunc('hour', m.event_timestamp)                       AS hour,
  count(*)                                                    AS meter_readings,
  sum(m.active_power_kw)                                      AS total_consumption_kw,
  avg(m.active_power_kw)                                      AS avg_power_kw,
  coalesce(sum(m.active_power_kw * p.price_eur_kwh), 0)       AS total_cost_eur,
  sum(m.active_power_kw) - coalesce(pv.pv_generation_kw, 0)   AS net_grid_kw
FROM silver.energy_meter m
LEFT JOIN silver.energy_price p
  ON p.site_id = m.site_id
 AND date_trunc('hour', p.event_timestamp) = date_trunc('hour', m.event_timestamp)
LEFT JOIN (SELECT site_id, date_trunc('hour', event_timestamp) AS hour, sum(power_kw) AS pv_generation_kw
           FROM silver.pv_generation GROUP BY 1, 2) pv
  ON pv.site_id = m.site_id AND pv.hour = date_trunc('hour', m.event_timestamp)
GROUP BY m.site_id, date_trunc('hour', m.event_timestamp), pv.pv_generation_kw;

CREATE MATERIALIZED VIEW IF NOT EXISTS gold.energy_balance_hourly AS
SELECT
  m.site_id,
  date_trunc('hour', m.event_timestamp)  AS hour,
  sum(m.active_power_kw)                  AS consumption_kw,
  coalesce(pv.pv_generation_kw, 0)       AS pv_generation_kw,
  sum(m.active_power_kw) - coalesce(pv.pv_generation_kw, 0) AS balance_kw
FROM silver.energy_meter m
LEFT JOIN (SELECT site_id, date_trunc('hour', event_timestamp) AS hour, sum(power_kw) AS pv_generation_kw
           FROM silver.pv_generation GROUP BY 1, 2) pv
  ON pv.site_id = m.site_id AND pv.hour = date_trunc('hour', m.event_timestamp)
GROUP BY m.site_id, date_trunc('hour', m.event_timestamp), pv.pv_generation_kw;

-- Phase 3 — analytics ---------------------------------------------------------
CREATE MATERIALIZED VIEW IF NOT EXISTS gold.anomaly_candidates AS
WITH stats AS (
  SELECT site_id, avg(active_power_kw) AS avg_kw, stddev(active_power_kw) AS std_kw
  FROM silver.energy_meter
  GROUP BY site_id
)
SELECT
  m.event_timestamp,
  m.site_id,
  m.meter_id,
  m.active_power_kw,
  round(((m.active_power_kw - s.avg_kw) / nullif(s.std_kw, 0))::numeric, 4) AS z_score,
  CASE WHEN abs((m.active_power_kw - s.avg_kw) / nullif(s.std_kw, 0)) > 3 THEN 'high'
       ELSE 'medium' END AS severity
FROM silver.energy_meter m
JOIN stats s ON s.site_id = m.site_id
WHERE s.std_kw IS NOT NULL AND s.std_kw > 0
  AND abs((m.active_power_kw - s.avg_kw) / s.std_kw) > 2;   -- z-score > 2σ (as in the full)

CREATE MATERIALIZED VIEW IF NOT EXISTS gold.energy_cost_daily AS
SELECT
  m.site_id,
  (m.event_timestamp::date)                   AS day,
  sum(m.active_power_kw)                      AS consumption_kwh,
  coalesce(sum(m.active_power_kw * p.price_eur_kwh), 0) AS total_cost_eur
FROM silver.energy_meter m
LEFT JOIN silver.energy_price p
  ON p.site_id = m.site_id AND (p.event_timestamp::date) = (m.event_timestamp::date)
GROUP BY m.site_id, (m.event_timestamp::date);

-- Phase 4 — dashboard summary (depends on the others) -------------------------
CREATE MATERIALIZED VIEW IF NOT EXISTS gold.city_dashboard_summary AS
SELECT
  m.site_id,
  max(m.event_timestamp)                                        AS updated_at,
  coalesce(sum(m.active_power_kw) FILTER (WHERE m.event_timestamp::date = current_date), 0)
                                                                AS consumption_kw_today,
  (SELECT count(*) FROM gold.anomaly_candidates a
    WHERE a.site_id = m.site_id
      AND a.event_timestamp >= current_date)                     AS active_anomalies
FROM silver.energy_meter m
GROUP BY m.site_id;

-- ---------------------------------------------------------------------------
-- gold_refresh(): full refresh of the matviews, dependency order (4 phases,
-- like GOLD_TABLE_ORDER in the full — spec §C1.5). Called by pg_cron.
-- ---------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION public.gold_refresh() RETURNS void
LANGUAGE plpgsql
AS $$
BEGIN
  -- phase 1
  REFRESH MATERIALIZED VIEW gold.weather_hourly;
  REFRESH MATERIALIZED VIEW gold.pv_performance_hourly;
  REFRESH MATERIALIZED VIEW gold.streetlight_zone_hourly;
  REFRESH MATERIALIZED VIEW gold.event_impact_daily;
  -- phase 2
  REFRESH MATERIALIZED VIEW gold.net_grid_hourly;
  REFRESH MATERIALIZED VIEW gold.energy_balance_hourly;
  -- phase 3
  REFRESH MATERIALIZED VIEW gold.anomaly_candidates;
  REFRESH MATERIALIZED VIEW gold.energy_cost_daily;
  -- phase 4
  REFRESH MATERIALIZED VIEW gold.city_dashboard_summary;
END;
$$;

-- ---------------------------------------------------------------------------
-- reader rights: SELECT on all gold matviews (data plane + catalog),
-- and on future matviews of the gold schema. No write (CA-S7).
-- ---------------------------------------------------------------------------
GRANT SELECT ON ALL TABLES IN SCHEMA gold TO reader;
ALTER DEFAULT PRIVILEGES IN SCHEMA gold GRANT SELECT ON TABLES TO reader;
-- explicit matviews (safety if ALL TABLES does not cover relkind='m')
GRANT SELECT ON gold.weather_hourly, gold.pv_performance_hourly,
  gold.streetlight_zone_hourly, gold.event_impact_daily,
  gold.net_grid_hourly, gold.energy_balance_hourly,
  gold.anomaly_candidates, gold.energy_cost_daily,
  gold.city_dashboard_summary TO reader;
