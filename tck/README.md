# FAP-light server TCK — user guide

Conformance kit (TCK) of the FAP-light server, derived from the **§10 acceptance criteria** of
`../spec/fap-light-spec-serveur.md`. It is the counterpart, for the light profile, of the `tck/` of the DSP
Connector of the full FAP (`/home/sylvain/facis/facis-fap-iot-ai/services/dsp-connector/tck`):
same contract requirements (DSP catalog, data plane `{assetId, schema, table, columns, rows,
rowCount}`, HMAC, JSON-LD `dspace:Error` errors, DLQ, watermarks, PG roles), executed against
the light target (PostgreSQL + ORCE, 1 VM).

The tests are **black-box**: they assume no internal implementation, only the contracts of the spec and the names declared in `tck.properties`.

## Prerequisites (launcher host)

| Tool | Why |
|---|---|
| `python3` (stdlib only) | harness |
| `psql` (postgresql-client) **or** docker with `pg.cmd` as a wrapper `docker compose … exec -T postgres psql` | SQL assertions per PG role |
| `sftp`/`ssh` (OpenSSH) + key `sftp.key` | CA-S2 file drops |
| network connectivity to the target VM (Postgres, ORCE:1880, SFTP:22) | — |

## Configuration

Copy `tck.properties` to `tck.properties.local` and fill in the `CHANGE_ME`
(host, passwords of the 4 PG roles `admin`/`gate`/`reader`/`app`, HMAC secret of the test
contract, SFTP credentials). Any key can be overridden by env: `dsp.base.url` →
`TCK_DSP_BASE_URL`.

Expected table/view names (configurable): `bronze.energy_meter`, `silver.energy_meter`,
`gold.net_grid_hourly`, tables `dlq`, `_cursors`, `silver._watermarks`, `dsp_access_audit`.

## Execution

```bash
cd tck
./run-tck.sh                                  # all suites
./run-tck.sh --suites CA-S4,CA-S5             # subset
./run-tck.sh --list                           # discovery without execution
./run-tck.sh --properties tck.properties.local
```

Output: console + `evidence/tck-run-<stamp>.log` + `evidence/junit-<stamp>.xml`
(exit code 0 = conformant, 1 = non-conformant, 2 = configuration error).

## Suite scope (mapping spec §10)

| Suite | AC | What is verified | Specifics |
|---|---|---|---|
| `test_ca_s1_ingestion_simulation.py` | CA-S1 | injected `sim.*` payload → bronze line (raw + lineage) → silver ≤ `silver.timeout.s` (default 600 s) → gold ≤ `gold.timeout.s` | unique marker per run (`TCK_RUN_ID`); typing verified via CAST |
| `test_ca_s2_ingestion_sftp.py` | CA-S2 | valid JSON/CSV SFTP drop → bronze + `/archive` archiving + cursor ; invalid file → DLQ with `error_type`/`message`/`ts` + raw payload | bounded wait at `sftp.cycle.s` + margin |
| `test_ca_s3_catalogue_dsp.py` | CA-S3 | catalog = exactly the assets of `expected/datasets.json` (gold-only, `format: postgres`), each with `policySummary` (rate_limit 10 req/min, ttl PT1H); an undeclared gold matview created on the fly does not appear | set comparison, not subset |
| `test_ca_s4_data_plane.py` | CA-S4 | response `{assetId, schema, table, columns, rows, rowCount}` ; ISO 8601 from/to window respected ; `rowCount ≤ 5000` (= 5000 if the window holds more than 5000 rows) ; without/bad HMAC token → 401/403 in `dspace:Error` ; unknown asset → 403/404 `dspace:Error` ; invalid from/to → 400 `dspace:Error` | canonical HMAC form defined by the TCK (see `target.py::dsp_sign`) |
| `test_ca_s5_rate_limit_audit.py` | CA-S5 | burst of `rate.limit+2` signed requests on a dedicated asset: ≤ 10 accepted (2xx), ≥ 2 rejected (429/403 `dspace:Error`) ; each accepted access is audited (`agreement_id`, timestamp) | dedicated asset `ca.s5.asset.id` so as not to pollute the CA-S4 bucket |
| `test_ca_s6_watermarks_full_refresh.py` | CA-S6 | silver watermark advances after ingesting a new bronze line ; `--full-refresh` replays all silver from bronze (replayed lines present, watermark = max bronze ts) ; silver quality filters apply (semantically invalid bronze line absent from silver, present in bronze) | `silver.refresh.mode=pg_cron` (poll) or `cli` (direct execution of `silver.materializer.cmd`) |
| `test_ca_s7_roles_pg.py` | CA-S7 | role `gate`: SELECT bronze denied, INSERT silver/gold denied, INSERT bronze accepted ; role `reader`: SELECT gold accepted, any write denied ; role `app`: write on `app.*` accepted, write on bronze denied ; the 4 roles exist | test by running psql per role, assertion on `permission denied` |
| `test_ca_s8_superset_gold.py` | CA-S8 | the exact dashboard queries (`expected/superset_queries.sql`) run unmodified as role `reader` on the gold matviews and return the expected columns | if `superset.base.url` + `superset.api.token` + `superset.chart.ids` are set: live check via the Superset API (chart data), otherwise documented SKIP |

## Canonical HMAC form (test contract)

The DSP data plane requires a per-request signature:

```
canonical = "POST\n{dsp.dataplane.path}\n{assetId}\n{from}\n{to}\n{X-DSP-Timestamp}"
X-DSP-Signature: sha256=HMAC_SHA256(secret, canonical)   (hex)
X-DSP-Agreement: {dsp.agreement.id}
X-DSP-Timestamp: {ISO 8601 UTC}
```

This is the reference form implemented by the TCK; the tested server must verify it.

## Out of scope of TCK v1 (reminder spec §1.3 / §8)

No tests on: LDP/JSON-LD endpoint, populated `app.*` domain, SSE/WebSocket push, ActivityPub,
Type Index, SHACL, catalog over `app.*` views. These topics belong to step 2.
