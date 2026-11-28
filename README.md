# FAP-light — Server (step 1)

Repository for the **FAP-light server** workstream (light profile of the FAP architecture, ISO FACIS scope):
a data server deployable on **1 VM** (PostgreSQL + ORCE/Node-RED + Caddy + IdP), exposing the
same data and protocol contracts as the full FAP, without Kafka / NiFi / Trino / Iceberg / Stackable.

- **Specification**: `../spec/fap-light-spec-serveur.md` (version 0.2 — decisions D-1..D-6 ratified in §9:
  MQTT naming, bronze lineage without Kafka, HMAC data-plane auth, Keycloak 26.x IdP, `format: postgres`)
- **References**: FAP architecture synthesis (`../spec/facis-fap-architecture-synthese.md`),
  profiles (`../spec/fap-domaine-app-et-profils.md`), use cases
  (`../spec/supervision-energetique-spec-fonctionnelle.md`), reference full implementation
  `/home/sylvain/facis/facis-fap-iot-ai`.

## Repository contents

```
fap-light-serveur/
├── README.md                  ← this file
├── docker-compose.yml         ← 1 VM stack: postgres + orce + mosquitto + sftp + caddy + keycloak
├── .env.example               ← variables (PG role passwords, DSP HMAC secret, …)
├── deploy/
│   ├── postgres/              ← PG16 image + pg_cron ; init SQL (roles, schemas, gate, views, matviews, cron)
│   │   └── init/              ← 01_roles 02_core 03_silver_views 04_gold_matviews 05_pg_cron
│   ├── orce/                  ← ORCE image (Node-RED) + flows (flows_gen.py -> data/flows.json)
│   │   └── data/              ← settings.js, flows.json, package.json, dsp-config/datasets.json
│   ├── caddy/  mosquitto/  keycloak/  sftp/
├── deploy/ai/                 ← AI stack (workstream 5, spec ../spec/app-ai-component-editor.md rev 5) :
│   │                             dedicated service, enabled by COMPOSE_PROFILES=ai, routed by caddy on /ai/*
│   │                             J15 delivered: POST /ai/run, text outputs, /ai_models/ registry, jobs, counters
│   │                             tests : cd deploy/ai && python3 -m unittest discover -s tests
├── materializers/
│   └── silver_materializer.py ← watermark CLI / --full-refresh (CI ; in prod pg_cron runs every */10 min)
├── scripts/                   ← deploy.sh, wait-healthy.sh, backup.sh (pg_dump + WAL, SPOF assumed)
└── tck/                       ← server conformance kit (TCK), derived from §10 of the spec
    ├── README.md              ← TCK user guide
    ├── run-tck.sh             ← entry point
    ├── tck.properties         ← configuration of the tested target (endpoints, PG roles, timeouts…)
    ├── expected/
    │   ├── datasets.json      ← expected gold assets in the DSP catalog (postgres format)
    │   └── superset_queries.sql ← exact Superset dashboard queries (CA-S8)
    └── harness/
        ├── runner.py          ← suite discovery/execution, JUnit report + evidence log
        ├── target.py          ← psql / HTTP / SFTP / HMAC clients towards the target
        ├── fixtures/          ← test data sets (simulation, SFTP)
        └── suites/            ← one suite per acceptance criterion CA-S1..CA-S8
```

## Deployment (1 VM)

```bash
cp .env.example .env        # edit the secrets (PG roles, DSP_HMAC_SECRET, …)
./scripts/deploy.sh         # build + up + health wait
# ORCE http://localhost:1880 | PG localhost:5432 | MQTT :1883 | SFTP :2222 | IdP :8080 | caddy https://$FAP_DOMAIN
./scripts/backup.sh         # timestamped pg_dump (backups/) — continuous WAL to be enabled in production
```

Implemented architecture (spec §3): ORCE (Node-RED) as a gate in front of PostgreSQL —
never direct SQL from the sources. Sources (simulation `POST /ingest/sim` + 60 s tick,
SFTP cycle 60 s, MQTT QoS bridge per route) go through the gate functions
(`public.gate_ingest` / `gate_reject`, called with the `gate` role) which validate the
envelope (mandatory fields, types, bounds, size → OK/DLQ) then insert into
`bronze.*` (JSONB + lineage, decision D-2). Routing on the envelope `type` field
(decision D-1). `silver.*` = typed views (CAST + quality filters),
`gold.*` = matviews refreshed by pg_cron (*/10 min), watermarks in
`silver._watermarks`. PG roles: `gate` (INSERT bronze only), `reader` (read gold),
`app` (app.*), `admin`. DSP catalog auto-derived (gold ∩ `datasets.json`,
`format: postgres`) ; data plane `POST /dsp/data` (HMAC D-3, rate limit 10/min/offer,
audit `dsp_access_audit`, `SELECT` + from/to + LIMIT 5000, JSON-LD errors
`dspace:Error`). IdP: Keycloak 26.x (decision D-4). Caddy as TLS front end.

## Covered acceptance criteria (spec §10)

| AC | Statement (summary) | Test suite |
|----|---------------------|------------|
| CA-S1 | Simulation data `sim.*`: bronze → silver (≤ 10 min) → gold, full lineage | `tck/harness/suites/test_ca_s1_ingestion_simulation.py` |
| CA-S2 | SFTP file ingested (60 s cycle), archived, cursor ; invalid file → DLQ (`error_type`/`message`/`ts`) | `tck/harness/suites/test_ca_s2_ingestion_sftp.py` |
| CA-S3 | DSP catalog = exactly the declared gold assets, with `policySummary` ; undeclared = absent | `tck/harness/suites/test_ca_s3_catalogue_dsp.py` |
| CA-S4 | Data plane: `{assetId, schema, table, columns, rows, rowCount}`, from/to window, LIMIT 5000 ; without contract → 401/403 `dspace:Error` | `tck/harness/suites/test_ca_s4_data_plane.py` |
| CA-S5 | Rate limit 10 req/min per offer ; accesses audited (`agreement_id`) | `tck/harness/suites/test_ca_s5_rate_limit_audit.py` |
| CA-S6 | Silver watermark advances ; `--full-refresh` replays all silver from bronze | `tck/harness/suites/test_ca_s6_watermarks_full_refresh.py` |
| CA-S7 | PG roles: `gate` no read, `reader` no write | `tck/harness/suites/test_ca_s7_roles_pg.py` |
| CA-S8 | Superset dashboards on gold over Postgres without modifying the queries | `tck/harness/suites/test_ca_s8_superset_gold.py` |

## TCK quick start

```bash
cd tck
cp tck.properties tck.properties.local   # adapt the target
./run-tck.sh --properties tck.properties.local
# evidence: tck/evidence/tck-run-<stamp>.log + junit-<stamp>.xml
```

See `tck/README.md` for prerequisites, detailed configuration and the exact scope of each test.

> Note: the `tck.properties` parser keeps inline comments (`key=value # comment`) —
> override the relevant keys via `TCK_*` environment variables (e.g. `TCK_DATA_LIMIT=5000`) rather
> than editing the file.

## Reference validation (real run, 2026-11-19)

Stack deployed locally (docker: postgres16+pg_cron, orce, mosquitto, sftp, keycloak, caddy),
TCK run end to end: **33 tests, 32 OK, 0 failure, 0 error, 1 skip** (CA-S8 live Superset API
test, skipped as long as `superset.base.url` is not set — by design).
Journal: `tck/evidence/tck-run-20261119T210657Z.log` + `junit-20261119T210657Z.xml`.

CA-S2 test_03 note: the `sftp_invalid.json` fixture is a deliberately invalid raw text file
(the `__MARKER__` substitution is effective there), so the deposited content contains the marker.
The DLQ payload of file rejections is the pure raw content (column `payload`),
read before any guard — the `fm in payload` assertion passes via the raw content itself.
