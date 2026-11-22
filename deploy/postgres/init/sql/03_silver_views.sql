-- ============================================================================
-- FAP-light — 03_silver_views.sql : typed silver views (->> + CAST), same
-- quality filters as the full FAP (spec §C1.2, synthesis §1). Silver = validity:
-- typed, filtered, replayable (full-refresh = reapplying the views, CA-S6).
-- The CAST in silver is the typing act.
-- ============================================================================

CREATE OR REPLACE VIEW silver.energy_meter AS
SELECT
  (raw_payload->>'timestamp')::timestamptz                AS event_timestamp,
  ingest_timestamp,
  raw_payload->>'site_id'                                 AS site_id,
  raw_payload->>'meter_id'                                AS meter_id,
  raw_payload->>'asset_id'                                AS asset_id,
  raw_payload->>'schema_version'                          AS schema_version,
  raw_payload->>'type'                                    AS type,
  (raw_payload->>'active_power_kw')::float8               AS active_power_kw,
  (raw_payload->>'active_energy_kwh_total')::float8       AS active_energy_kwh_total,
  (raw_payload->'readings'->>'active_power_l1_w')::float8 AS active_power_l1_w,
  (raw_payload->'readings'->>'active_power_l2_w')::float8 AS active_power_l2_w,
  (raw_payload->'readings'->>'active_power_l3_w')::float8 AS active_power_l3_w,
  (raw_payload->'readings'->>'voltage_l1_v')::float8      AS voltage_l1_v,
  (raw_payload->'readings'->>'voltage_l2_v')::float8      AS voltage_l2_v,
  (raw_payload->'readings'->>'voltage_l3_v')::float8      AS voltage_l3_v,
  (raw_payload->'readings'->>'current_l1_a')::float8      AS current_l1_a,
  (raw_payload->'readings'->>'current_l2_a')::float8      AS current_l2_a,
  (raw_payload->'readings'->>'current_l3_a')::float8      AS current_l3_a,
  (raw_payload->'readings'->>'power_factor')::float8      AS power_factor,
  (raw_payload->'readings'->>'frequency_hz')::float8      AS frequency_hz,
  (raw_payload->'readings'->>'total_energy_kwh')::float8  AS total_energy_kwh,
  -- derived field (same rule as the full): total_active_power_w = L1+L2+L3
  (coalesce((raw_payload->'readings'->>'active_power_l1_w')::float8,0)
   + coalesce((raw_payload->'readings'->>'active_power_l2_w')::float8,0)
   + coalesce((raw_payload->'readings'->>'active_power_l3_w')::float8,0)) AS total_active_power_w,
  source_topic,
  source_system,
  source_seq
FROM bronze.energy_meter
-- quality filters (identical to the full: non-null timestamp, meter_id present,
-- power >= 0)
WHERE raw_payload->>'timestamp' IS NOT NULL
  AND raw_payload->>'meter_id' IS NOT NULL
  AND raw_payload->>'active_power_kw' IS NOT NULL
  AND (raw_payload->>'active_power_kw')::float8 >= 0;

CREATE OR REPLACE VIEW silver.pv_generation AS
SELECT
  (raw_payload->>'timestamp')::timestamptz          AS event_timestamp,
  ingest_timestamp,
  raw_payload->>'site_id'                           AS site_id,
  raw_payload->>'pv_system_id'                      AS pv_system_id,
  raw_payload->>'type'                              AS type,
  (raw_payload->>'power_kw')::float8                AS power_kw,
  (raw_payload->>'energy_kwh_today')::float8        AS energy_kwh_today,
  (raw_payload->>'capacity_kw')::float8             AS capacity_kw,
  CASE WHEN (raw_payload->>'capacity_kw')::float8 > 0
       THEN round(((raw_payload->>'power_kw')::float8 / (raw_payload->>'capacity_kw')::float8)::numeric, 4)
       ELSE NULL END                               AS capacity_factor,
  source_topic, source_system, source_seq
FROM bronze.pv_generation
WHERE raw_payload->>'timestamp' IS NOT NULL
  AND raw_payload->>'pv_system_id' IS NOT NULL
  AND raw_payload->>'power_kw' IS NOT NULL
  AND (raw_payload->>'power_kw')::float8 >= 0;

CREATE OR REPLACE VIEW silver.energy_price AS
SELECT
  (raw_payload->>'timestamp')::timestamptz  AS event_timestamp,
  ingest_timestamp,
  raw_payload->>'site_id'                   AS site_id,
  raw_payload->>'type'                      AS type,
  (raw_payload->>'price_eur_kwh')::float8   AS price_eur_kwh,
  source_topic, source_system, source_seq
FROM bronze.energy_price
WHERE raw_payload->>'timestamp' IS NOT NULL
  AND raw_payload->>'site_id' IS NOT NULL
  AND raw_payload->>'price_eur_kwh' IS NOT NULL;

CREATE OR REPLACE VIEW silver.weather AS
SELECT
  (raw_payload->>'timestamp')::timestamptz        AS event_timestamp,
  ingest_timestamp,
  raw_payload->>'site_id'                         AS site_id,
  raw_payload->>'type'                            AS type,
  (raw_payload->>'temperature_c')::float8         AS temperature_c,
  (raw_payload->>'irradiance_wm2')::float8        AS irradiance_wm2,
  (raw_payload->>'humidity_pct')::float8          AS humidity_pct,
  (raw_payload->>'wind_speed_ms')::float8         AS wind_speed_ms,
  (raw_payload->>'cloud_cover_pct')::float8       AS cloud_cover_pct,
  source_topic, source_system, source_seq
FROM bronze.weather
WHERE raw_payload->>'timestamp' IS NOT NULL
  AND raw_payload->>'site_id' IS NOT NULL
  AND raw_payload->>'temperature_c' IS NOT NULL;

CREATE OR REPLACE VIEW silver.consumer_load AS
SELECT
  (raw_payload->>'timestamp')::timestamptz  AS event_timestamp,
  ingest_timestamp,
  raw_payload->>'site_id'                   AS site_id,
  raw_payload->>'consumer_id'               AS consumer_id,
  raw_payload->>'type'                      AS type,
  (raw_payload->>'load_kw')::float8         AS load_kw,
  source_topic, source_system, source_seq
FROM bronze.consumer_load
WHERE raw_payload->>'timestamp' IS NOT NULL
  AND raw_payload->>'consumer_id' IS NOT NULL
  AND (raw_payload->>'load_kw')::float8 >= 0;

CREATE OR REPLACE VIEW silver.streetlight AS
SELECT
  (raw_payload->>'timestamp')::timestamptz  AS event_timestamp,
  ingest_timestamp,
  raw_payload->>'zone_id'                   AS zone_id,
  raw_payload->>'type'                      AS type,
  (raw_payload->>'dim_level')::float8       AS dim_level,
  (raw_payload->>'power_kw')::float8        AS power_kw,
  source_topic, source_system, source_seq
FROM bronze.streetlight
WHERE raw_payload->>'timestamp' IS NOT NULL
  AND raw_payload->>'zone_id' IS NOT NULL
  AND (raw_payload->>'dim_level')::float8 BETWEEN 0 AND 100;

CREATE OR REPLACE VIEW silver.traffic AS
SELECT
  (raw_payload->>'timestamp')::timestamptz  AS event_timestamp,
  ingest_timestamp,
  raw_payload->>'zone_id'                   AS zone_id,
  raw_payload->>'type'                      AS type,
  (raw_payload->>'vehicle_count')::int      AS vehicle_count,
  source_topic, source_system, source_seq
FROM bronze.traffic
WHERE raw_payload->>'timestamp' IS NOT NULL
  AND raw_payload->>'zone_id' IS NOT NULL
  AND (raw_payload->>'vehicle_count')::int >= 0;

CREATE OR REPLACE VIEW silver.city_event AS
SELECT
  (raw_payload->>'timestamp')::timestamptz  AS event_timestamp,
  ingest_timestamp,
  raw_payload->>'zone_id'                   AS zone_id,
  raw_payload->>'type'                      AS type,
  raw_payload->>'event_type'                AS event_type,
  (raw_payload->>'severity')::int           AS severity,
  source_topic, source_system, source_seq
FROM bronze.city_event
WHERE raw_payload->>'timestamp' IS NOT NULL
  AND raw_payload->>'zone_id' IS NOT NULL
  AND raw_payload->>'event_type' IN ('ACCIDENT','EMERGENCY','EVENT');  -- enum Avro

CREATE OR REPLACE VIEW silver.city_weather AS
SELECT
  (raw_payload->>'timestamp')::timestamptz  AS event_timestamp,
  ingest_timestamp,
  raw_payload->>'city_id'                   AS city_id,
  raw_payload->>'type'                      AS type,
  (raw_payload->>'temperature_c')::float8   AS temperature_c,
  (raw_payload->>'humidity_pct')::float8    AS humidity_pct,
  source_topic, source_system, source_seq
FROM bronze.city_weather
WHERE raw_payload->>'timestamp' IS NOT NULL
  AND raw_payload->>'city_id' IS NOT NULL;
