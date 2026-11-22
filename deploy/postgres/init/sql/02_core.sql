-- ============================================================================
-- FAP-light — 02_core.sql : schemas, cross-cutting tables, bronze tables,
-- gate functions (SECURITY DEFINER), rights of the 4 roles.
-- Spec §C1.2 (schemas), §C1.4 (gate + roles), §5 (DLQ), decisions D-1/D-2 (§9).
-- ============================================================================

CREATE SCHEMA IF NOT EXISTS bronze;
CREATE SCHEMA IF NOT EXISTS silver;
CREATE SCHEMA IF NOT EXISTS gold;
CREATE SCHEMA IF NOT EXISTS app;   -- reserved for step 2, empty in v1 (spec §8)

-- ---------------------------------------------------------------------------
-- Cross-cutting tables (public schema, names expected by the TCK)
-- ---------------------------------------------------------------------------

-- Table-based DLQ, raw payload preserved (spec §5)
CREATE TABLE IF NOT EXISTS public.dlq (
  id           bigserial PRIMARY KEY,
  error_type   text NOT NULL,
  message      text NOT NULL,
  ts           timestamptz NOT NULL DEFAULT now(),
  payload      text,                 -- raw payload
  file_name    text,                 -- SFTP origin where applicable
  source_topic text
);

-- Cursors (processed SFTP files, offsets) in a table (spec §C1.4)
CREATE TABLE IF NOT EXISTS public._cursors (
  file_name text PRIMARY KEY,
  status    text NOT NULL,           -- 'processed' | 'rejected'
  ts        timestamptz NOT NULL DEFAULT now(),
  detail    text
);

-- Data space access audit (agreement_id, timestamps) — spec §5, CA-S5
CREATE TABLE IF NOT EXISTS public.dsp_access_audit (
  id           bigserial PRIMARY KEY,
  agreement_id text NOT NULL,
  asset_id     text,
  ts           timestamptz NOT NULL DEFAULT now(),
  status       int,
  path         text
);
CREATE INDEX IF NOT EXISTS dsp_access_audit_asset_ts_idx
  ON public.dsp_access_audit (asset_id, ts DESC);

-- Agreements (DSP control plane): agreement_id -> asset (NULL = wildcard)
CREATE TABLE IF NOT EXISTS public.dsp_agreements (
  agreement_id        text PRIMARY KEY,
  asset_id            text,
  active              boolean NOT NULL DEFAULT true,
  rate_limit_per_min  int NOT NULL DEFAULT 10,
  created_at          timestamptz NOT NULL DEFAULT now()
);
INSERT INTO public.dsp_agreements (agreement_id, asset_id)
VALUES (:'dsp_agreement_id', NULL)
ON CONFLICT (agreement_id) DO NOTHING;

-- Silver watermarks (spec §C1.5): key = 'silver.<bronze table>', watermark as
-- ISO 8601 UTC text (lexically comparable to the ISO timestamps of the tests).
CREATE TABLE IF NOT EXISTS silver._watermarks (
  key        text PRIMARY KEY,
  watermark  text NOT NULL,          -- ISO 8601 UTC, e.g. 2026-11-19T15:30:00Z
  updated_at timestamptz NOT NULL DEFAULT now()
);

-- ---------------------------------------------------------------------------
-- Bronze tables: raw JSONB + lineage (decision D-2, spec §C1.2)
--   raw_payload (JSONB, fidelity) + source_topic (real origin, D-1),
--   source_system, ingest_timestamp, message_key, source_seq (uuid, replaces
--   kafka_partition/kafka_offset absent without Kafka).
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS bronze.energy_meter (
  id               bigserial PRIMARY KEY,
  raw_payload      jsonb NOT NULL,
  source_topic     text NOT NULL,
  source_system    text NOT NULL,
  ingest_timestamp timestamptz NOT NULL DEFAULT now(),
  message_key      text,
  source_seq       uuid NOT NULL DEFAULT gen_random_uuid()
);
CREATE TABLE IF NOT EXISTS bronze.pv_generation (
  id bigserial PRIMARY KEY, raw_payload jsonb NOT NULL, source_topic text NOT NULL,
  source_system text NOT NULL, ingest_timestamp timestamptz NOT NULL DEFAULT now(),
  message_key text, source_seq uuid NOT NULL DEFAULT gen_random_uuid()
);
CREATE TABLE IF NOT EXISTS bronze.weather (
  id bigserial PRIMARY KEY, raw_payload jsonb NOT NULL, source_topic text NOT NULL,
  source_system text NOT NULL, ingest_timestamp timestamptz NOT NULL DEFAULT now(),
  message_key text, source_seq uuid NOT NULL DEFAULT gen_random_uuid()
);
CREATE TABLE IF NOT EXISTS bronze.energy_price (
  id bigserial PRIMARY KEY, raw_payload jsonb NOT NULL, source_topic text NOT NULL,
  source_system text NOT NULL, ingest_timestamp timestamptz NOT NULL DEFAULT now(),
  message_key text, source_seq uuid NOT NULL DEFAULT gen_random_uuid()
);
CREATE TABLE IF NOT EXISTS bronze.consumer_load (
  id bigserial PRIMARY KEY, raw_payload jsonb NOT NULL, source_topic text NOT NULL,
  source_system text NOT NULL, ingest_timestamp timestamptz NOT NULL DEFAULT now(),
  message_key text, source_seq uuid NOT NULL DEFAULT gen_random_uuid()
);
CREATE TABLE IF NOT EXISTS bronze.streetlight (
  id bigserial PRIMARY KEY, raw_payload jsonb NOT NULL, source_topic text NOT NULL,
  source_system text NOT NULL, ingest_timestamp timestamptz NOT NULL DEFAULT now(),
  message_key text, source_seq uuid NOT NULL DEFAULT gen_random_uuid()
);
CREATE TABLE IF NOT EXISTS bronze.traffic (
  id bigserial PRIMARY KEY, raw_payload jsonb NOT NULL, source_topic text NOT NULL,
  source_system text NOT NULL, ingest_timestamp timestamptz NOT NULL DEFAULT now(),
  message_key text, source_seq uuid NOT NULL DEFAULT gen_random_uuid()
);
CREATE TABLE IF NOT EXISTS bronze.city_event (
  id bigserial PRIMARY KEY, raw_payload jsonb NOT NULL, source_topic text NOT NULL,
  source_system text NOT NULL, ingest_timestamp timestamptz NOT NULL DEFAULT now(),
  message_key text, source_seq uuid NOT NULL DEFAULT gen_random_uuid()
);
CREATE TABLE IF NOT EXISTS bronze.city_weather (
  id bigserial PRIMARY KEY, raw_payload jsonb NOT NULL, source_topic text NOT NULL,
  source_system text NOT NULL, ingest_timestamp timestamptz NOT NULL DEFAULT now(),
  message_key text, source_seq uuid NOT NULL DEFAULT gen_random_uuid()
);

-- Indexes: indexable raw data (JSONB GIN) + lineage (spec §C1.2 "indexable")
DO $$
DECLARE t text;
BEGIN
  FOREACH t IN ARRAY ARRAY['energy_meter','pv_generation','weather','energy_price',
                          'consumer_load','streetlight','traffic','city_event','city_weather']
  LOOP
    EXECUTE format('CREATE INDEX IF NOT EXISTS %I_raw_gin ON bronze.%I USING gin (raw_payload jsonb_path_ops)', t || '_raw_gin', t);
    EXECUTE format('CREATE INDEX IF NOT EXISTS %I_ingest_idx ON bronze.%I (ingest_timestamp)', t || '_ingest_idx', t);
    EXECUTE format('CREATE INDEX IF NOT EXISTS %I_topic_idx ON bronze.%I (source_topic)', t || '_topic_idx', t);
  END LOOP;
END $$;

-- ---------------------------------------------------------------------------
-- LIKE operator on JSONB: the bronze raw data is stored as jsonb (fidelity);
-- conformance / supervision queries test `raw_payload LIKE '%...%'`.
-- We add the ~~ (jsonb, text) operator (the jsonb->text cast exists but
-- is not implicit).
-- ---------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION public.jsonb_text_like(jsonb, text) RETURNS boolean
LANGUAGE sql IMMUTABLE PARALLEL SAFE AS
$$ SELECT $1::text LIKE $2 $$;

DROP OPERATOR IF EXISTS public.~~ (jsonb, text);
CREATE OPERATOR public.~~ (
  LEFTARG = jsonb, RIGHTARG = text, PROCEDURE = public.jsonb_text_like
);

-- ---------------------------------------------------------------------------
-- Gate: binary structural conformance OK/DLQ (spec §C1.4)
-- SECURITY DEFINER functions (owner = admin) called by the gate role:
-- the gate has no direct right on dlq/_cursors (CA-S7), it goes through the gate.
-- Routing on the `type` field of the unified envelope (decision D-1 §9).
-- ---------------------------------------------------------------------------

-- Mapping table envelope type -> bronze table (D-1)
CREATE TABLE IF NOT EXISTS public.gate_type_map (
  envelope_type text PRIMARY KEY,
  bronze_table  text NOT NULL,
  required_fields text[] NOT NULL,
  numeric_fields  text[] NOT NULL DEFAULT '{}'
);
INSERT INTO public.gate_type_map (envelope_type, bronze_table, required_fields, numeric_fields) VALUES
  ('smart_energy.meter',    'energy_meter',   ARRAY['type','timestamp','site_id','meter_id','active_power_kw'],        ARRAY['active_power_kw','active_energy_kwh_total']),
  ('smart_energy.pv',       'pv_generation',  ARRAY['type','timestamp','site_id','pv_system_id','power_kw'],            ARRAY['power_kw','energy_kwh_today']),
  ('smart_energy.price',    'energy_price',   ARRAY['type','timestamp','site_id','price_eur_kwh'],                      ARRAY['price_eur_kwh']),
  ('smart_energy.weather',  'weather',        ARRAY['type','timestamp','site_id','temperature_c'],                      ARRAY['temperature_c','irradiance_wm2','humidity_pct','wind_speed_ms','cloud_cover_pct']),
  ('smart_energy.consumer', 'consumer_load',  ARRAY['type','timestamp','site_id','consumer_id','load_kw'],              ARRAY['load_kw']),
  ('smart_city.light',      'streetlight',    ARRAY['type','timestamp','zone_id','dim_level'],                          ARRAY['dim_level','power_kw']),
  ('smart_city.traffic',    'traffic',        ARRAY['type','timestamp','zone_id','vehicle_count'],                      ARRAY['vehicle_count']),
  ('smart_city.event',      'city_event',     ARRAY['type','timestamp','zone_id','event_type','severity'],              ARRAY['severity']),
  ('smart_city.weather',    'city_weather',   ARRAY['type','timestamp','city_id','temperature_c'],                      ARRAY['temperature_c'])
ON CONFLICT (envelope_type) DO NOTHING;

-- Envelope validation + INSERT bronze, or DLQ. Called by the gate role.
CREATE OR REPLACE FUNCTION public.gate_ingest(
  p_type          text,
  p_payload       jsonb,
  p_source_topic  text,
  p_source_system text,
  p_message_key   text DEFAULT NULL,
  p_file_name     text DEFAULT NULL
) RETURNS jsonb
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public, bronze, pg_temp
AS $$
DECLARE
  v_map        record;
  v_missing    text;
  v_badnum     text;
  v_iso_ok     boolean;
  v_size       int;
  v_reason     text;
BEGIN
  -- 1. known type? (routing on `type`, decision D-1)
  SELECT * INTO v_map FROM public.gate_type_map WHERE envelope_type = p_type;
  IF NOT FOUND THEN
    v_reason := 'unknown_type';
    INSERT INTO public.dlq (error_type, message, ts, payload, file_name, source_topic)
    VALUES (v_reason, 'unknown envelope type: ' || coalesce(p_type, '<null>'),
            now(), p_payload::text, p_file_name, p_source_topic);
    PERFORM public._gate_cursor(p_file_name, 'rejected', v_reason);
    RETURN jsonb_build_object('ok', false, 'dlq', true, 'reason', v_reason);
  END IF;

  -- 2. structural conformance: object payload, bounded size (1 MB)
  IF p_payload IS NULL OR jsonb_typeof(p_payload) <> 'object' THEN
    v_reason := 'invalid_payload';
  ELSE
    v_size := length(p_payload::text);
    IF v_size > 1048576 THEN
      v_reason := 'payload_too_large';
    END IF;
  END IF;

  -- 3. mandatory fields + types (ISO 8601 timestamp, numeric fields)
  IF v_reason IS NULL THEN
    SELECT string_agg(f, ', ') INTO v_missing
    FROM unnest(v_map.required_fields) f
    WHERE NOT (p_payload ? f);
    IF v_missing IS NOT NULL THEN
      v_reason := 'missing_fields: ' || v_missing;
    END IF;
  END IF;
  IF v_reason IS NULL THEN
    v_iso_ok := (p_payload->>'timestamp') ~ '^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d+)?(Z|[+-]\d{2}:\d{2})$';
    IF NOT v_iso_ok THEN
      v_reason := 'invalid_timestamp';
    END IF;
  END IF;
  IF v_reason IS NULL THEN
    SELECT string_agg(f, ', ') INTO v_badnum
    FROM unnest(v_map.numeric_fields) f
    WHERE p_payload ? f AND jsonb_typeof(p_payload->f) NOT IN ('number');
    IF v_badnum IS NOT NULL THEN
      v_reason := 'invalid_numeric_fields: ' || v_badnum;
    END IF;
  END IF;

  IF v_reason IS NOT NULL THEN
    INSERT INTO public.dlq (error_type, message, ts, payload, file_name, source_topic)
    VALUES ('envelope_invalid', v_reason, now(), p_payload::text, p_file_name, p_source_topic);
    PERFORM public._gate_cursor(p_file_name, 'rejected', v_reason);
    RETURN jsonb_build_object('ok', false, 'dlq', true, 'reason', v_reason);
  END IF;

  -- 4. OK: INSERT bronze (atomic = received = durable, spec §C1.4)
  EXECUTE format(
    'INSERT INTO bronze.%I (raw_payload, source_topic, source_system, ingest_timestamp, message_key)
     VALUES ($1, $2, $3, now(), $4)', v_map.bronze_table)
  USING p_payload, p_source_topic, p_source_system, p_message_key;

  PERFORM public._gate_cursor(p_file_name, 'processed', NULL);
  RETURN jsonb_build_object('ok', true, 'table', v_map.bronze_table);
END;
$$;

-- Unparseable rejection (e.g. invalid JSON on the SFTP side): DLQ with raw payload.
CREATE OR REPLACE FUNCTION public.gate_reject(
  p_error_type  text,
  p_message     text,
  p_payload     text,
  p_file_name   text DEFAULT NULL,
  p_source_topic text DEFAULT NULL
) RETURNS jsonb
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public, pg_temp
AS $$
BEGIN
  INSERT INTO public.dlq (error_type, message, ts, payload, file_name, source_topic)
  VALUES (p_error_type, p_message, now(), p_payload, p_file_name, p_source_topic);
  PERFORM public._gate_cursor(p_file_name, 'rejected', p_error_type);
  RETURN jsonb_build_object('ok', false, 'dlq', true, 'reason', p_error_type);
END;
$$;

-- Internal cursor (used by the functions above)
CREATE OR REPLACE FUNCTION public._gate_cursor(p_file_name text, p_status text, p_detail text)
RETURNS void
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public, pg_temp
AS $$
BEGIN
  IF p_file_name IS NULL THEN RETURN; END IF;
  INSERT INTO public._cursors (file_name, status, ts, detail)
  VALUES (p_file_name, p_status, now(), p_detail)
  ON CONFLICT (file_name) DO UPDATE SET status = EXCLUDED.status, ts = EXCLUDED.ts, detail = EXCLUDED.detail;
END;
$$;

-- ---------------------------------------------------------------------------
-- Rights (CA-S7)
-- ---------------------------------------------------------------------------

-- gate: INSERT bronze only, EXECUTE on the gate, no read at all.
-- (USAGE/SELECT on sequences: needed for the bigserial columns)
GRANT USAGE ON SCHEMA bronze TO gate;
GRANT INSERT ON ALL TABLES IN SCHEMA bronze TO gate;
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA bronze TO gate;
ALTER DEFAULT PRIVILEGES IN SCHEMA bronze GRANT INSERT ON TABLES TO gate;
ALTER DEFAULT PRIVILEGES IN SCHEMA bronze GRANT USAGE, SELECT ON SEQUENCES TO gate;
REVOKE EXECUTE ON FUNCTION public.gate_ingest(text, jsonb, text, text, text, text) FROM PUBLIC;
REVOKE EXECUTE ON FUNCTION public.gate_reject(text, text, text, text, text) FROM PUBLIC;
REVOKE EXECUTE ON FUNCTION public._gate_cursor(text, text, text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION public.gate_ingest(text, jsonb, text, text, text, text) TO gate;
GRANT EXECUTE ON FUNCTION public.gate_reject(text, text, text, text, text) TO gate;

-- reader: gold read only (will set the explicit GRANTs in 04_gold_matviews.sql)
GRANT USAGE ON SCHEMA gold TO reader;

-- app: read/write on app.* only (reserved for step 2)
GRANT USAGE, CREATE ON SCHEMA app TO app;
GRANT ALL ON ALL TABLES IN SCHEMA app TO app;
ALTER DEFAULT PRIVILEGES IN SCHEMA app GRANT ALL ON TABLES TO app;

-- ---------------------------------------------------------------------------
-- Silver watermark: updated on each bronze INSERT (trigger) + by
-- silver_tick() via pg_cron every 10 min (spec §C1.5). The trigger makes
-- the advance deterministic (CA-S6); cron remains the nominal cadence.
-- Watermark stored as ISO 8601 UTC text (safe lexical comparison).
-- ---------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION public.bronze_watermark_trigger() RETURNS trigger
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public, silver, pg_temp
AS $$
DECLARE
  v_wm text;
BEGIN
  v_wm := to_char(NEW.ingest_timestamp AT TIME ZONE 'UTC', 'YYYY-MM-DD"T"HH24:MI:SS"Z"');
  INSERT INTO silver._watermarks (key, watermark, updated_at)
  VALUES ('silver.' || TG_TABLE_NAME, v_wm, now())
  ON CONFLICT (key) DO UPDATE
    SET watermark = GREATEST(silver._watermarks.watermark, EXCLUDED.watermark),
        updated_at = now();
  RETURN NEW;
END;
$$;

DO $$
DECLARE t text;
BEGIN
  FOREACH t IN ARRAY ARRAY['energy_meter','pv_generation','weather','energy_price',
                          'consumer_load','streetlight','traffic','city_event','city_weather']
  LOOP
    EXECUTE format('DROP TRIGGER IF EXISTS trg_wm_%I ON bronze.%I', t, t);
    EXECUTE format('CREATE TRIGGER trg_wm_%I AFTER INSERT ON bronze.%I FOR EACH ROW EXECUTE FUNCTION public.bronze_watermark_trigger()', t, t);
  END LOOP;
END $$;

-- ---------------------------------------------------------------------------
-- silver_tick(): advances the silver watermarks from bronze (spec §C1.5, CA-S6)
-- ---------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION public.silver_tick() RETURNS void
LANGUAGE plpgsql
AS $$
DECLARE
  r record;
  v_wm text;
BEGIN
  FOR r IN SELECT table_name FROM information_schema.tables
           WHERE table_schema = 'bronze' AND table_type = 'BASE TABLE'
  LOOP
    EXECUTE format(
      'SELECT coalesce(to_char(max(ingest_timestamp) AT TIME ZONE ''UTC'', ''YYYY-MM-DD"T"HH24:MI:SS"Z"''), ''1970-01-01T00:00:00Z'')
       FROM bronze.%I', r.table_name) INTO v_wm;
    INSERT INTO silver._watermarks (key, watermark, updated_at)
    VALUES ('silver.' || r.table_name, v_wm, now())
    ON CONFLICT (key) DO UPDATE SET watermark = EXCLUDED.watermark, updated_at = EXCLUDED.updated_at;
  END LOOP;
END;
$$;
