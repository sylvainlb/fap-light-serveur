#!/usr/bin/env python3
"""Génère deploy/orce/data/flows.json — flows Node-RED (ORCE) du serveur FAP-light.

Tabs :
  ingest-sim   : POST /ingest/sim -> gate -> INSERT bronze (rôle gate)
                 + tick de simulation (toutes les 60 s, 9 feeds, seedrandom)
  ingest-sftp  : cycle 60 s, garde extension/taille, parsers JSON/CSV,
                 archive /archive, curseur, DLQ (spec §C1.3, CA-S2)
  ingest-mqtt  : pont MQTT, QoS par route (meter/prices/pv=1, weather/city=0),
                 source_topic = topic MQTT d'origine (décision D-1)
  dsp          : POST /dsp/catalogue/request (auto-dérivation gold, CA-S3)
               + POST /dsp/data (data plane : HMAC D-3, rate limit 10/min,
                 audit, SELECT from/to LIMIT 5000, erreurs dspace:Error, CA-S4/S5)
  health       : GET /health

La logique SQL passe par les fonctions gate dédiées (owner admin) appelées avec
le rôle gate, et par des pools pg dédiés par rôle (gate/reader/admin).
"""

import json
from pathlib import Path

# ---------------------------------------------------------------- helpers JS
JS_POOL = """
function pool(role) {
  const { Pool } = pgLib;
  const key = 'pgpool_' + role;
  let p = global.get(key);
  if (!p) {
    p = new Pool({
      host: env.get('PG_HOST') || 'postgres',
      port: parseInt(env.get('PG_PORT') || '5432', 10),
      database: env.get('PG_DB') || 'fap',
      user: env.get('PG_' + role.toUpperCase() + '_USER') || role,
      password: env.get('PG_' + role.toUpperCase() + '_PASSWORD') || '',
      max: 5
    });
    global.set(key, p);
  }
  return p;
}
"""

JS_SIM_TOPIC = """
const SIM_TOPIC = {
  'smart_energy.meter': 'sim.smart_energy.meter',
  'smart_energy.pv': 'sim.smart_energy.pv',
  'smart_energy.price': 'sim.smart_energy.price',
  'smart_energy.weather': 'sim.smart_energy.weather',
  'smart_energy.consumer': 'sim.smart_energy.consumer',
  'smart_city.light': 'sim.smart_city.light',
  'smart_city.traffic': 'sim.smart_city.traffic',
  'smart_city.event': 'sim.smart_city.event',
  'smart_city.weather': 'sim.smart_city.weather'
};
"""

JS_DSP_ERROR = """
function dspError(status, code, reasons) {
  return { statusCode: status, payload: {
    '@context': 'https://w3id.org/dspace/2025/1/context.jsonld',
    '@type': 'dspace:Error', 'dspace:code': code, 'dspace:reason': reasons } };
}
"""

# ------------------------------------------------------------- fn : /ingest/sim
FN_GATE_SIM = JS_POOL + JS_SIM_TOPIC + """
(async () => {
  try {
    const p = msg.payload || {};
    const type = p.type;
    const topic = SIM_TOPIC[type] || ('sim.' + String(type || 'unknown'));
    const key = p.meter_id || p.pv_system_id || p.consumer_id || p.zone_id || p.city_id || null;
    const res = await pool('gate').query(
      'SELECT public.gate_ingest($1,$2,$3,$4,$5,$6) AS r',
      [type, JSON.stringify(p), topic, 'simulation', key, null]);
    const r = res.rows[0].r;
    msg.statusCode = r.ok ? 200 : 202;
    msg.payload = r;
    node.send(msg);
  } catch (e) {
    msg.statusCode = 500;
    msg.payload = { error: String(e && e.message || e) };
    node.send(msg);
  }
  node.done();
})();
"""

# ------------------------------------------------------------- fn : sim tick
FN_SIM_CYCLE = JS_POOL + JS_SIM_TOPIC + """
(async () => {
  try {
    const site = env.get('SIM_SITE_ID') || 'site-berlin-001';
    const now = new Date().toISOString();
    const rnd = seedrandom((env.get('SIM_SEED') || 'fap-light') + now.slice(0, 13));
    const f = (a, b, d) => +(a + rnd() * (b - a)).toFixed(d === undefined ? 2 : d);
    const payloads = [
      { type: 'smart_energy.meter', schema_version: '1.0', timestamp: now, site_id: site,
        meter_id: 'janitza-umg96rm-001', asset_id: 'janitza-umg96rm-001',
        active_power_kw: f(5, 30), active_energy_kwh_total: f(54000, 55000),
        readings: { active_power_l1_w: f(1500, 5000, 1), active_power_l2_w: f(1500, 5000, 1),
          active_power_l3_w: f(1500, 5000, 1), voltage_l1_v: 230.5, voltage_l2_v: 229.8,
          voltage_l3_v: 231.2, current_l1_a: f(5, 20, 1), current_l2_a: f(5, 20, 1),
          current_l3_a: f(5, 20, 1), power_factor: 0.97, frequency_hz: 50.01, total_energy_kwh: 54321.67 } },
      { type: 'smart_energy.pv', schema_version: '1.0', timestamp: now, site_id: site,
        pv_system_id: 'pv-rooftop-001', power_kw: f(2, 10), energy_kwh_today: f(5, 40), capacity_kw: 12 },
      { type: 'smart_energy.price', schema_version: '1.0', timestamp: now, site_id: site, price_eur_kwh: f(0.15, 0.35, 4) },
      { type: 'smart_energy.weather', schema_version: '1.0', timestamp: now, site_id: site,
        temperature_c: f(-2, 28), irradiance_wm2: f(0, 900, 0), humidity_pct: f(20, 95, 0),
        wind_speed_ms: f(0, 12), cloud_cover_pct: f(0, 100, 0) },
      { type: 'smart_energy.consumer', schema_version: '1.0', timestamp: now, site_id: site,
        consumer_id: 'consumer-001', load_kw: f(0.5, 6) },
      { type: 'smart_city.light', schema_version: '1.0', timestamp: now, zone_id: 'zone-north',
        dim_level: f(20, 100, 1), power_kw: f(0.5, 4) },
      { type: 'smart_city.traffic', schema_version: '1.0', timestamp: now, zone_id: 'zone-north',
        vehicle_count: Math.floor(rnd() * 500) },
      { type: 'smart_city.event', schema_version: '1.0', timestamp: now, zone_id: 'zone-north',
        event_type: ['ACCIDENT', 'EMERGENCY', 'EVENT'][Math.floor(rnd() * 3)], severity: 1 + Math.floor(rnd() * 5) },
      { type: 'smart_city.weather', schema_version: '1.0', timestamp: now, city_id: 'berlin',
        temperature_c: f(-3, 29), humidity_pct: f(20, 95, 0) }
    ];
    const p = pool('gate');
    for (const pl of payloads) {
      await p.query('SELECT public.gate_ingest($1,$2,$3,$4,$5,$6) AS r',
        [pl.type, JSON.stringify(pl), SIM_TOPIC[pl.type], 'simulation',
         pl.meter_id || pl.pv_system_id || pl.consumer_id || pl.zone_id || pl.city_id || null, null]);
    }
  } catch (e) {
    node.error(e, msg);
  }
  node.done();
})();
"""

# ------------------------------------------------------------- fn : SFTP cycle
FN_SFTP_CYCLE = JS_POOL + """
function coerceCsvRow(r) {
  const out = {};
  for (const k of Object.keys(r)) {
    const v = r[k];
    if (v === null || v === undefined || v === '') { out[k] = v; continue; }
    if (/(_kw|_w|_kwh|_total|_c|_pct|_ms|_v|_a|_hz|_eur_kwh|_count|_level|_factor)$/.test(k) && !isNaN(Number(v))) out[k] = Number(v);
    else out[k] = v;
  }
  return out;
}
(async () => {
  const sftp = new Sftp();
  try {
    await sftp.connect({
      host: env.get('SFTP_HOST') || 'sftp',
      port: parseInt(env.get('SFTP_PORT') || '22', 10),
      username: env.get('SFTP_USER') || 'fap',
      password: env.get('SFTP_PASSWORD') || ''
    });
    const ingestDir = env.get('SFTP_INGEST_DIR') || '/ingest';
    const archiveDir = env.get('SFTP_ARCHIVE_DIR') || '/archive';
    const maxBytes = parseInt(env.get('SFTP_MAX_BYTES') || '1048576', 10);
    const p = pool('gate');
    const list = await sftp.list(ingestDir);
    for (const f of list) {
      if (f.type !== '-') continue;
      const name = f.name;
      const remote = ingestDir + '/' + name;
      let buf = null;
      try {
        buf = await sftp.get(remote);   // lu avant la garde : le DLQ garde le brut même en cas de rejet
        if (!/\\.(json|csv)$/i.test(name)) {
          const e = new Error('extension non autorisée (attendu .json/.csv)'); e.code = 'bad_extension'; throw e;
        }
        if (buf.length > maxBytes) {
          const e = new Error('taille > ' + maxBytes + ' octets'); e.code = 'file_too_large'; throw e;
        }
        let payloads;
        if (/\\.csv$/i.test(name)) {
          payloads = csvSync.parse(buf.toString('utf8'), { columns: true, skip_empty_lines: true }).map(coerceCsvRow);
        } else {
          payloads = [JSON.parse(buf.toString('utf8'))];
        }
        for (const pl of payloads) {
          await p.query('SELECT public.gate_ingest($1,$2,$3,$4,$5,$6) AS r',
            [pl.type, JSON.stringify(pl), 'sftp:' + name, 'sftp',
             pl.meter_id || pl.pv_system_id || pl.consumer_id || pl.zone_id || pl.city_id || null, name]);
        }
      } catch (e) {
        try {
          await p.query('SELECT public.gate_reject($1,$2,$3,$4,$5) AS r',
            [e.code || 'parse_error', String(e && e.message || e),
             JSON.stringify({ file_name: name, raw_payload: buf ? buf.toString('utf8') : '' }), name, 'sftp:' + name]);
        } catch (e2) { node.error(e2, msg); }
      }
      try { await sftp.rename(remote, archiveDir + '/' + name); }
      catch (e) { node.warn('archive failed: ' + name + ' ' + e.message); }
    }
  } catch (e) {
    node.error(e, msg);
  } finally {
    try { await sftp.end(); } catch (_) {}
  }
  node.done();
})();
"""

# ------------------------------------------------------------- fn : MQTT -> gate
FN_MQTT_GATE = JS_POOL + """
(async () => {
  try {
    let pl = msg.payload;
    if (Buffer.isBuffer(pl)) pl = pl.toString('utf8');
    if (typeof pl === 'string') pl = JSON.parse(pl);
    await pool('gate').query('SELECT public.gate_ingest($1,$2,$3,$4,$5,$6) AS r',
      [pl.type, JSON.stringify(pl), msg.topic, 'mqtt',
       pl.meter_id || pl.pv_system_id || pl.consumer_id || pl.zone_id || pl.city_id || null, null]);
  } catch (e) {
    try {
      await pool('gate').query('SELECT public.gate_reject($1,$2,$3,$4,$5) AS r',
        ['mqtt_parse_error', String(e && e.message || e), String(msg.payload), null, msg.topic]);
    } catch (e2) { node.error(e2, msg); }
  }
  node.done();
})();
"""

# ------------------------------------------------------------- fn : catalogue DSP
FN_CATALOGUE = JS_POOL + JS_DSP_ERROR + """
(async () => {
  try {
    const datasets = JSON.parse(fs.readFileSync(env.get('DSP_DATASETS_PATH') || '/data/dsp-config/datasets.json', 'utf8'));
    // auto-dérivation depuis le catalogue PG (gold uniquement à l'ISO, spec §C1.6) :
    // on ne publie que les tables gold EXISTANTES (visibles par le rôle reader)
    // ET déclarées dans datasets.json.
    const res = await pool('reader').query(
      "SELECT c.relname FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace " +
      "WHERE n.nspname = 'gold' AND c.relkind IN ('m','r','v')");
    const gold = new Set(res.rows.map(r => r.relname));
    const out = (Array.isArray(datasets) ? datasets : [])
      .filter(d => d && d.metadata && d.metadata.schema === 'gold' && gold.has(d.metadata.table));
    msg.statusCode = 200;
    msg.payload = { datasets: out };
    msg.headers = { 'Content-Type': 'application/json' };
    node.send(msg);
  } catch (e) {
    const err = dspError(500, 'catalogue_unavailable', [String(e && e.message || e)]);
    msg.statusCode = err.statusCode; msg.payload = err.payload;
    node.send(msg);
  }
  node.done();
})();
"""

# ------------------------------------------------------------- fn : data plane
FN_DATAPLANE = JS_POOL + JS_DSP_ERROR + """
(async () => {
  function fail(err) {
    msg.statusCode = err.statusCode;
    msg.payload = err.payload;
    msg.headers = { 'Content-Type': 'application/json' };
    node.send(msg);
  }
  try {
    const h = msg.req.headers;
    const sig = h['x-dsp-signature'];
    const agreement = h['x-dsp-agreement'];
    const ts = h['x-dsp-timestamp'];
    const body = msg.payload || {};
    const assetId = body.assetId, from = body.from, to = body.to;
    const path = env.get('DSP_DATA_PATH') || '/dsp/data';

    // 1. contrat présent (headers HMAC + body) — sinon 401
    if (!sig || !agreement || !ts || !assetId || !from || !to) {
      return fail(dspError(401, 'missing_credentials',
        ['X-DSP-Signature, X-DSP-Agreement, X-DSP-Timestamp et body {assetId, from, to} requis']));
    }
    // 2. vérification HMAC — forme canonique D-3 (spec §9) :
    //    sha256=HMAC_SHA256(secret, "POST\\n{path}\\n{assetId}\\n{from}\\n{to}\\n{ts}")
    const secret = env.get('DSP_HMAC_SECRET') || '';
    const canonical = ['POST', path, assetId, from, to, ts].join('\\n');
    const expected = 'sha256=' + crypto.createHmac('sha256', secret).update(canonical).digest('hex');
    const a = Buffer.from(String(sig)), b = Buffer.from(expected);
    if (a.length !== b.length || !crypto.timingSafeEqual(a, b)) {
      return fail(dspError(403, 'invalid_signature', ['HMAC invalide']));
    }
    // 3. fenêtre from/to ISO 8601 — sinon 400
    const ISO_RE = /^\\d{4}-\\d{2}-\\d{2}T\\d{2}:\\d{2}:\\d{2}(\\.\\d+)?(Z|[+-]\\d{2}:\\d{2})$/;
    if (!ISO_RE.test(from) || !ISO_RE.test(to)) {
      return fail(dspError(400, 'invalid_window', ['from/to doivent être des timestamps ISO 8601']));
    }
    // 4. plan de contrôle : agreement connu et actif
    const ag = await pool('admin').query(
      'SELECT agreement_id, asset_id, rate_limit_per_min FROM public.dsp_agreements WHERE agreement_id = $1 AND active = true',
      [agreement]);
    if (ag.rowCount === 0) return fail(dspError(403, 'unknown_agreement', ['agreement_id inconnu ou inactif']));
    const rateLimit = ag.rows[0].rate_limit_per_min || 10;
    // 5. asset déclaré dans le catalogue (gold-only à l'ISO) — sinon 404
    const datasets = JSON.parse(fs.readFileSync(env.get('DSP_DATASETS_PATH') || '/data/dsp-config/datasets.json', 'utf8'));
    const ds = (Array.isArray(datasets) ? datasets : []).find(d => d && d.id === assetId);
    if (!ds || !ds.metadata || !ds.metadata.schema || !ds.metadata.table) {
      return fail(dspError(404, 'asset_not_found', ['asset non déclaré dans le catalogue: ' + assetId]));
    }
    if (ag.rows[0].asset_id && ag.rows[0].asset_id !== assetId) {
      return fail(dspError(403, 'agreement_not_covering_asset', ["l'agreement ne couvre pas cet asset"]));
    }
    const schema = ds.metadata.schema, table = ds.metadata.table, timeColumn = ds.metadata.timeColumn;
    // 6. rate limit par offre (asset), fenêtre glissante 60 s — sinon 429 audité
    const rl = await pool('admin').query(
      "SELECT count(*)::int AS n FROM public.dsp_access_audit WHERE asset_id = $1 AND status = 200 AND ts > now() - interval '60 seconds'",
      [assetId]);
    if (rl.rows[0].n >= rateLimit) {
      await pool('admin').query(
        'INSERT INTO public.dsp_access_audit (agreement_id, asset_id, ts, status, path) VALUES ($1, $2, now(), 429, $3)',
        [agreement, assetId, path]);
      return fail(dspError(429, 'rate_limit_exceeded', ['rate limit ' + rateLimit + ' req/min par offre dépassé']));
    }
    // 7. data plane (rôle reader) : SELECT + fenêtre from/to + LIMIT 5000 (spec §C1.7)
    const sql = 'SELECT * FROM ' + schema + '.' + table +
      ' WHERE ' + timeColumn + ' >= $1::timestamptz AND ' + timeColumn + ' <= $2::timestamptz' +
      ' ORDER BY ' + timeColumn + ' LIMIT 5000';
    const q = await pool('reader').query(sql, [from, to]);
    const columns = q.fields.map(f => f.name);
    const rows = q.rows.map(r => columns.map(c => r[c]));
    // 8. audit de l'accès (agreement_id, ts)
    await pool('admin').query(
      'INSERT INTO public.dsp_access_audit (agreement_id, asset_id, ts, status, path) VALUES ($1, $2, now(), 200, $3)',
      [agreement, assetId, path]);
    msg.statusCode = 200;
    msg.payload = { assetId: assetId, schema: schema, table: table, columns: columns, rows: rows, rowCount: rows.length };
    msg.headers = { 'Content-Type': 'application/json' };
    node.send(msg);
  } catch (e) {
    node.error(e, msg);
    fail(dspError(500, 'internal_error', [String(e && e.message || e)]));
  }
  node.done();
})();
"""

FN_HEALTH = """
msg.statusCode = 200;
msg.payload = { status: 'ok', service: 'fap-light-orce' };
node.send(msg);
node.done();
"""

# ------------------------------------------------------------------- assembly
def node(id, type, name, z, x=100, y=100, **props):
    n = {"id": id, "type": type, "name": name, "z": z, "x": x, "y": y}
    n.update(props)
    return n

flows = []

flows.append({"id": "tab-ingest-sim", "type": "tab", "label": "ingest-sim", "disabled": False,
              "info": "POST /ingest/sim + tick simulation -> gate -> bronze"})
flows.append({"id": "tab-ingest-sftp", "type": "tab", "label": "ingest-sftp", "disabled": False,
              "info": "cycle SFTP 60 s -> gate -> bronze, archive, curseur, DLQ"})
flows.append({"id": "tab-ingest-mqtt", "type": "tab", "label": "ingest-mqtt", "disabled": False,
              "info": "pont MQTT -> gate -> bronze (QoS par route)"})
flows.append({"id": "tab-dsp", "type": "tab", "label": "dsp", "disabled": False,
              "info": "catalogue DSP + data plane DSP (HMAC, rate limit, audit)"})
flows.append({"id": "tab-health", "type": "tab", "label": "health", "disabled": False, "info": ""})

by_id = {}

def add(n):
    flows.append(n)
    by_id[n["id"]] = n
    return n

def wire(src, dst):
    by_id[src].setdefault("wires", [[]])[0].append(dst)

# --- ingest-sim
add(node("n-in-sim", "http in", "POST /ingest/sim", "tab-ingest-sim", 100, 100, url="/ingest/sim", method="post"))
add(node("n-fn-gate-sim", "function", "gate -> bronze (role gate)", "tab-ingest-sim", 320, 100, func=FN_GATE_SIM, outputs=1, libs=[{"module":"pg","var":"pgLib"}]))
add(node("n-res-sim", "http response", "", "tab-ingest-sim", 560, 100))
add(node("n-sim-tick", "inject", "tick simulation 60s", "tab-ingest-sim", 100, 180, repeat="60", once=True, onceDelay=5))
add(node("n-fn-sim", "function", "génère 9 feeds (seedrandom) -> gate", "tab-ingest-sim", 340, 180, func=FN_SIM_CYCLE, outputs=1, libs=[{"module":"pg","var":"pgLib"},{"module":"seedrandom","var":"seedrandom"}]))
wire("n-in-sim", "n-fn-gate-sim"); wire("n-fn-gate-sim", "n-res-sim"); wire("n-sim-tick", "n-fn-sim")

# --- ingest-sftp
add(node("n-sftp-tick", "inject", "cycle SFTP 60s", "tab-ingest-sftp", 100, 100, repeat="60", once=True, onceDelay=10))
add(node("n-fn-sftp", "function", "poll /ingest -> garde -> parse -> gate -> archive + curseur", "tab-ingest-sftp", 380, 100, func=FN_SFTP_CYCLE, outputs=1, libs=[{"module":"pg","var":"pgLib"},{"module":"ssh2-sftp-client","var":"Sftp"},{"module":"csv-parse/sync","var":"csvSync"}]))
wire("n-sftp-tick", "n-fn-sftp")

# --- ingest-mqtt
add(node("n-mqtt-broker", "mqtt-broker", "mosquitto", "tab-ingest-mqtt", 0, 0,
         broker="mosquitto", port="1883", clientid="fap-light-orce"))
MQTT_ROUTES = [  # (topic, qos) — QoS par route : meter/prices/pv = 1, weather/city = 0 (spec §C1.3)
    ("facis/energy/meter/+", "1"),
    ("facis/prices/spot", "1"),
    ("facis/energy/pv/+", "1"),
    ("facis/weather/current", "0"),
    ("facis/loads/+", "0"),
    ("facis/city/lights/+", "0"),
    ("facis/city/traffic/+", "0"),
    ("facis/city/events/+", "0"),
    ("facis/city/weather/+", "0"),
]
add(node("n-fn-mqtt", "function", "MQTT -> enveloppe -> gate (source_topic = topic d'origine, D-1)",
         "tab-ingest-mqtt", 380, 300, func=FN_MQTT_GATE, outputs=1, libs=[{"module":"pg","var":"pgLib"}]))
for i, (topic, qos) in enumerate(MQTT_ROUTES):
    add(node(f"n-mqtt-{i}", "mqtt in", topic, "tab-ingest-mqtt", 120, 100 + i * 60,
             topic=topic, qos=qos, broker="n-mqtt-broker"))
    wire(f"n-mqtt-{i}", "n-fn-mqtt")

# --- dsp
add(node("n-in-cat", "http in", "POST /dsp/catalogue/request", "tab-dsp", 100, 100, url="/dsp/catalogue/request", method="post"))
add(node("n-fn-cat", "function", "catalogue DSP (auto-dérivé gold ∩ datasets.json)", "tab-dsp", 340, 100, func=FN_CATALOGUE, outputs=1, libs=[{"module":"pg","var":"pgLib"},{"module":"fs","var":"fs"}]))
add(node("n-res-cat", "http response", "", "tab-dsp", 580, 100))
add(node("n-in-data", "http in", "POST /dsp/data", "tab-dsp", 100, 200, url="/dsp/data", method="post"))
add(node("n-fn-data", "function", "data plane DSP (HMAC D-3, rate limit, audit, LIMIT 5000)", "tab-dsp", 340, 200, func=FN_DATAPLANE, outputs=1, libs=[{"module":"pg","var":"pgLib"},{"module":"crypto","var":"crypto"},{"module":"fs","var":"fs"}]))
add(node("n-res-data", "http response", "", "tab-dsp", 580, 200))
wire("n-in-cat", "n-fn-cat"); wire("n-fn-cat", "n-res-cat")
wire("n-in-data", "n-fn-data"); wire("n-fn-data", "n-res-data")

# --- health
add(node("n-in-health", "http in", "GET /health", "tab-health", 100, 100, url="/health", method="get"))
add(node("n-fn-health", "function", "", "tab-health", 320, 100, func=FN_HEALTH, outputs=1))
add(node("n-res-health", "http response", "", "tab-health", 520, 100))
wire("n-in-health", "n-fn-health"); wire("n-fn-health", "n-res-health")

out = Path(__file__).resolve().parent / "data" / "flows.json"
out.parent.mkdir(parents=True, exist_ok=True)
out.write_text(json.dumps(flows, indent=1, ensure_ascii=False), encoding="utf-8")
print(f"flows.json généré: {out} ({len(flows)} nœuds)")
