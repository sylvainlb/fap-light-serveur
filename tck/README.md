# TCK serveur FAP-light — mode d'emploi

Kit de conformité (TCK) du serveur FAP-light, dérivé des **critères d'acceptation §10** de
`../spec/fap-light-spec-serveur.md`. C'est le pendant, pour le profil light, du `tck/` du DSP
Connector de la FAP-full (`/home/sylvain/facis/facis-fap-iot-ai/services/dsp-connector/tck`) :
mêmes exigences de contrat (catalogue DSP, data plane `{assetId, schema, table, columns, rows,
rowCount}`, HMAC, erreurs JSON-LD `dspace:Error`, DLQ, watermarks, rôles PG), exécutées contre
la cible light (PostgreSQL + ORCE, 1 VM).

Les tests sont **black-box** : ils ne supposent aucune implémentation interne, seulement les
contrats de la spec et les noms déclarés dans `tck.properties`.

## Prérequis (poste lanceur)

| Outil | Pourquoi |
|---|---|
| `python3` (stdlib seul) | harness |
| `psql` (postgresql-client) **ou** docker avec `pg.cmd` en wrapper `docker compose … exec -T postgres psql` | assertions SQL par rôle PG |
| `sftp`/`ssh` (OpenSSH) + clé `sftp.key` | dépôts de fichiers CA-S2 |
| connectivité réseau vers la VM cible (Postgres, ORCE:1880, SFTP:22) | — |

## Configuration

Copier `tck.properties` en `tck.properties.local` et renseigner les `CHANGE_ME`
(hôte, mots de passe des 4 rôles PG `admin`/`gate`/`reader`/`app`, secret HMAC du contrat
de test, credentials SFTP). Toute clé peut être surchargée par env : `dsp.base.url` →
`TCK_DSP_BASE_URL`.

Noms de tables/vues attendus (modifiables) : `bronze.energy_meter`, `silver.energy_meter`,
`gold.net_grid_hourly`, tables `dlq`, `_cursors`, `silver._watermarks`, `dsp_access_audit`.

## Exécution

```bash
cd tck
./run-tck.sh                                  # toutes les suites
./run-tck.sh --suites CA-S4,CA-S5             # sous-ensemble
./run-tck.sh --list                           # découverte sans exécution
./run-tck.sh --properties tck.properties.local
```

Sortie : console + `evidence/tck-run-<stamp>.log` + `evidence/junit-<stamp>.xml`
(code retour 0 = conforme, 1 = non conforme, 2 = erreur de configuration).

## Portée des suites (mapping spec §10)

| Suite | CA | Ce qui est vérifié | Particularités |
|---|---|---|---|
| `test_ca_s1_ingestion_simulation.py` | CA-S1 | payload `sim.*` injecté → ligne bronze (brut + lineage) → silver ≤ `silver.timeout.s` (défaut 600 s) → gold ≤ `gold.timeout.s` | marqueur unique par run (`TCK_RUN_ID`) ; typage vérifié via CAST |
| `test_ca_s2_ingestion_sftp.py` | CA-S2 | dépôt SFTP JSON/CSV valide → bronze + archivage `/archive` + curseur ; fichier invalide → DLQ avec `error_type`/`message`/`ts` + payload brut | attente bornée à `sftp.cycle.s` + marge |
| `test_ca_s3_catalogue_dsp.py` | CA-S3 | catalogue = exactement les assets de `expected/datasets.json` (gold-only, `format: postgres`), chacun avec `policySummary` (rate_limit 10 req/min, ttl PT1H) ; un matview gold non déclarée créée à la volée n'apparaît pas | comparaison d'ensemble, pas de sous-ensemble |
| `test_ca_s4_data_plane.py` | CA-S4 | réponse `{assetId, schema, table, columns, rows, rowCount}` ; fenêtre from/to ISO 8601 respectée ; `rowCount ≤ 5000` (= 5000 si la fenêtre contient plus de 5000 lignes) ; sans/with mauvais token HMAC → 401/403 en `dspace:Error` ; asset inconnu → 403/404 `dspace:Error` ; from/to invalide → 400 `dspace:Error` | forme canonique HMAC définie par le TCK (voir `target.py::dsp_sign`) |
| `test_ca_s5_rate_limit_audit.py` | CA-S5 | salve de `rate.limit+2` requêtes signées sur un asset dédié : ≤ 10 acceptées (2xx), ≥ 2 rejetées (429/403 `dspace:Error`) ; chaque accès accepté est audité (`agreement_id`, timestamp) | asset dédié `ca.s5.asset.id` pour ne pas polluer le bucket de CA-S4 |
| `test_ca_s6_watermarks_full_refresh.py` | CA-S6 | watermark silver avance après ingestion d'une nouvelle ligne bronze ; `--full-refresh` rejoue tout le silver depuis bronze (lignes rejouées présentes, watermark = max ts bronze) ; les filtres qualité silver s'appliquent (ligne bronze invalide sémantiquement absente du silver, présente en bronze) | `silver.refresh.mode=pg_cron` (poll) ou `cli` (exécution directe de `silver.materializer.cmd`) |
| `test_ca_s7_roles_pg.py` | CA-S7 | rôle `gate` : SELECT bronze refusé, INSERT silver/gold refusé, INSERT bronze accepté ; rôle `reader` : SELECT gold accepté, toute écriture refusée ; rôle `app` : écriture `app.*` acceptée, écriture bronze refusée ; les 4 rôles existent | test par exécution psql par rôle, assertion sur `permission denied` |
| `test_ca_s8_superset_gold.py` | CA-S8 | les requêtes exactes des dashboards (`expected/superset_queries.sql`) s'exécutent sans modification en rôle `reader` sur les matviews gold et retournent les colonnes attendues | si `superset.base.url` + `superset.api.token` + `superset.chart.ids` renseignés : contrôle live via l'API Superset (chart data), sinon SKIP documenté |

## Forme canonique HMAC (contrat de test)

Le data plane DSP exige une signature par requête :

```
canonical = "POST\n{dsp.dataplane.path}\n{assetId}\n{from}\n{to}\n{X-DSP-Timestamp}"
X-DSP-Signature: sha256=HMAC_SHA256(secret, canonical)   (hex)
X-DSP-Agreement: {dsp.agreement.id}
X-DSP-Timestamp: {ISO 8601 UTC}
```

C'est la forme de référence implémentée par le TCK ; le serveur testé doit la vérifier.

## Hors périmètre du TCK v1 (rappel spec §1.3 / §8)

Pas de test sur : endpoint LDP/JSON-LD, domaine `app.*` peuplé, push SSE/WebSocket, ActivityPub,
Type Index, SHACL, catalogue sur vues `app.*`. Ces sujets appartiennent à l'étape 2.
