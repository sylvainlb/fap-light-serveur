-- ============================================================================
-- FAP-light — 02_core.sql : schémas, tables transverses, tables bronze,
-- fonctions gate (SECURITY DEFINER), droits des 4 rôles.
-- Spec §C1.2 (schémas), §C1.4 (gate + rôles), §5 (DLQ), décisions D-1/D-2 (§9).
-- ============================================================================

CREATE SCHEMA IF NOT EXISTS bronze;
CREATE SCHEMA IF NOT EXISTS silver;
CREATE SCHEMA IF NOT EXISTS gold;
CREATE SCHEMA IF NOT EXISTS app;   -- réservé étape 2, vide en v1 (spec §8)

-- ---------------------------------------------------------------------------
-- Tables transverses (schéma public, noms attendus par le TCK)
-- ---------------------------------------------------------------------------

-- DLQ en table, payload brut conservé (spec §5)
CREATE TABLE IF NOT EXISTS public.dlq (
  id           bigserial PRIMARY KEY,
  error_type   text NOT NULL,
  message      text NOT NULL,
  ts           timestamptz NOT NULL DEFAULT now(),
  payload      text,                 -- payload brut
  file_name    text,                 -- origine SFTP le cas échéant
  source_topic text
);

-- Curseurs (fichiers SFTP traités, offsets) en table (spec §C1.4)
CREATE TABLE IF NOT EXISTS public._cursors (
  file_name text PRIMARY KEY,
  status    text NOT NULL,           -- 'processed' | 'rejected'
  ts        timestamptz NOT NULL DEFAULT now(),
  detail    text
);

-- Audit des accès data space (agreement_id, timestamps) — spec §5, CA-S5
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

-- Accords (plan de contrôle DSP) : agreement_id -> asset (NULL = wildcard)
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

-- Watermarks silver (spec §C1.5) : clé = 'silver.<table bronze>', watermark en
-- texte ISO 8601 UTC (comparable lexicalment aux timestamps ISO des tests).
CREATE TABLE IF NOT EXISTS silver._watermarks (
  key        text PRIMARY KEY,
  watermark  text NOT NULL,          -- ISO 8601 UTC, ex. 2026-11-19T15:30:00Z
  updated_at timestamptz NOT NULL DEFAULT now()
);

-- ---------------------------------------------------------------------------
-- Tables bronze : JSONB brut + lineage (décision D-2, spec §C1.2)
--   raw_payload (JSONB, fidélité) + source_topic (origine réelle, D-1),
--   source_system, ingest_timestamp, message_key, source_seq (uuid, remplace
--   kafka_partition/kafka_offset absents sans Kafka).
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

-- Index : brut indexable (JSONB GIN) + lineage (spec §C1.2 « indexable »)
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
-- Opérateur LIKE sur JSONB : le brut bronze est stocké en jsonb (fidélité) ;
-- les requêtes de conformité / supervision testent `raw_payload LIKE '%...%'`.
-- On ajoute l'opérateur ~~ (jsonb, text) (le cast jsonb->text existe mais
-- n'est pas implicite).
-- ---------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION public.jsonb_text_like(jsonb, text) RETURNS boolean
LANGUAGE sql IMMUTABLE PARALLEL SAFE AS
$$ SELECT $1::text LIKE $2 $$;

DROP OPERATOR IF EXISTS public.~~ (jsonb, text);
CREATE OPERATOR public.~~ (
  LEFTARG = jsonb, RIGHTARG = text, PROCEDURE = public.jsonb_text_like
);

-- ---------------------------------------------------------------------------
-- Gate : conformité structurelle binaire OK/DLQ (spec §C1.4)
-- Fonctions SECURITY DEFINER (owner = admin) appelées par le rôle gate :
-- le gate n'a aucun droit direct sur dlq/_cursors (CA-S7), il passe par la gate.
-- Routage sur le champ `type` de l'enveloppe unifiée (décision D-1 §9).
-- ---------------------------------------------------------------------------

-- Table de correspondance type d'enveloppe -> table bronze (D-1)
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

-- Validation d'enveloppe + INSERT bronze, ou DLQ. Appelée par le rôle gate.
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
  -- 1. type connu ? (routage sur `type`, décision D-1)
  SELECT * INTO v_map FROM public.gate_type_map WHERE envelope_type = p_type;
  IF NOT FOUND THEN
    v_reason := 'unknown_type';
    INSERT INTO public.dlq (error_type, message, ts, payload, file_name, source_topic)
    VALUES (v_reason, 'type d''enveloppe inconnu: ' || coalesce(p_type, '<null>'),
            now(), p_payload::text, p_file_name, p_source_topic);
    PERFORM public._gate_cursor(p_file_name, 'rejected', v_reason);
    RETURN jsonb_build_object('ok', false, 'dlq', true, 'reason', v_reason);
  END IF;

  -- 2. conformité structurelle : payload objet, taille bornée (1 Mo)
  IF p_payload IS NULL OR jsonb_typeof(p_payload) <> 'object' THEN
    v_reason := 'invalid_payload';
  ELSE
    v_size := length(p_payload::text);
    IF v_size > 1048576 THEN
      v_reason := 'payload_too_large';
    END IF;
  END IF;

  -- 3. champs obligatoires + types (timestamp ISO 8601, champs numériques)
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

  -- 4. OK : INSERT bronze (atomique = reçu = durable, spec §C1.4)
  EXECUTE format(
    'INSERT INTO bronze.%I (raw_payload, source_topic, source_system, ingest_timestamp, message_key)
     VALUES ($1, $2, $3, now(), $4)', v_map.bronze_table)
  USING p_payload, p_source_topic, p_source_system, p_message_key;

  PERFORM public._gate_cursor(p_file_name, 'processed', NULL);
  RETURN jsonb_build_object('ok', true, 'table', v_map.bronze_table);
END;
$$;

-- Rejet non parsable (ex. JSON invalide côté SFTP) : DLQ avec payload brut.
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

-- Curseur interne (utilisé par les fonctions ci-dessus)
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
-- Droits (CA-S7)
-- ---------------------------------------------------------------------------

-- gate : INSERT bronze only, EXECUTE sur la gate, aucune lecture.
-- (USAGE/SELECT sur les séquences : nécessaire pour les colonnes bigserial)
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

-- reader : lecture gold uniquement (posera les GRANT explicites dans 04_gold_matviews.sql)
GRANT USAGE ON SCHEMA gold TO reader;

-- app : lecture/écriture sur app.* uniquement (réservé étape 2)
GRANT USAGE, CREATE ON SCHEMA app TO app;
GRANT ALL ON ALL TABLES IN SCHEMA app TO app;
ALTER DEFAULT PRIVILEGES IN SCHEMA app GRANT ALL ON TABLES TO app;

-- ---------------------------------------------------------------------------
-- Watermark silver : tenu à jour à chaque INSERT bronze (trigger) + par
-- silver_tick() via pg_cron toutes les 10 min (spec §C1.5). Le trigger rend
-- l'avancement déterministe (CA-S6) ; le cron reste la cadence nominale.
-- Watermark stocké en texte ISO 8601 UTC (comparaison lexicale sûre).
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
-- silver_tick() : avance les watermarks silver depuis bronze (spec §C1.5, CA-S6)
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
