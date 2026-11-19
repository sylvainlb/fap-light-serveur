# FAP-light — Serveur (étape 1)

Dépôt du chantier **serveur FAP-light** (profil light de l'architecture FAP, périmètre ISO FACIS) :
un serveur de données déployable sur **1 VM** (PostgreSQL + ORCE/Node-RED + caddy + IdP), exposant les
mêmes contrats de données et de protocoles que la FAP-full, sans Kafka / NiFi / Trino / Iceberg / Stackable.

- **Spécification** : `../spec/fap-light-spec-serveur.md` (version 0.2 — décisions D-1..D-6 actées en §9 :
  nommage MQTT, lineage bronze sans Kafka, auth data plane HMAC, IdP Keycloak 26.x, `format: postgres`)
- **Références** : synthèse d'architecture FAP (`../spec/facis-fap-architecture-synthese.md`),
  profils (`../spec/fap-domaine-app-et-profils.md`), cas d'usage
  (`../spec/supervision-energetique-spec-fonctionnelle.md`), implémentation full de référence
  `/home/sylvain/facis/facis-fap-iot-ai`.

## Contenu du dépôt

```
fap-light-serveur/
├── README.md                  ← ce fichier
├── docker-compose.yml         ← stack 1 VM : postgres + orce + mosquitto + sftp + caddy + keycloak
├── .env.example               ← variables (mots de passe rôles PG, secret HMAC DSP, …)
├── deploy/
│   ├── postgres/              ← image PG16 + pg_cron ; init SQL (rôles, schémas, gate, vues, matviews, cron)
│   │   └── init/              ← 01_roles 02_core 03_silver_views 04_gold_matviews 05_pg_cron
│   ├── orce/                  ← image ORCE (Node-RED) + flows (flows_gen.py -> data/flows.json)
│   │   └── data/              ← settings.js, flows.json, package.json, dsp-config/datasets.json
│   ├── caddy/  mosquitto/  keycloak/  sftp/
├── materializers/
│   └── silver_materializer.py ← CLI watermark / --full-refresh (CI ; en prod pg_cron tourne à */10 min)
├── scripts/                   ← deploy.sh, wait-healthy.sh, backup.sh (pg_dump + WAL, SPOF assumé)
└── tck/                       ← kit de conformité serveur (TCK), dérivé du §10 de la spec
    ├── README.md              ← mode d'emploi du TCK
    ├── run-tck.sh             ← point d'entrée
    ├── tck.properties         ← configuration de la cible testée (endpoints, rôles PG, timeouts…)
    ├── expected/
    │   ├── datasets.json      ← assets gold attendus dans le catalogue DSP (format postgres)
    │   └── superset_queries.sql ← requêtes exactes des dashboards Superset (CA-S8)
    └── harness/
        ├── runner.py          ← découverte/exécution des suites, rapport JUnit + evidence log
        ├── target.py          ← clients psql / HTTP / SFTP / HMAC vers la cible
        ├── fixtures/          ← jeux de données de test (simulation, SFTP)
        └── suites/            ← une suite par critère d'acceptation CA-S1..CA-S8
```

## Déploiement (1 VM)

```bash
cp .env.example .env        # éditer les secrets (rôles PG, DSP_HMAC_SECRET, …)
./scripts/deploy.sh         # build + up + attente santé
# ORCE http://localhost:1880 | PG localhost:5432 | MQTT :1883 | SFTP :2222 | IdP :8080 | caddy https://$FAP_DOMAIN
./scripts/backup.sh         # pg_dump horodaté (backups/) — WAL continu à activer en production
```

Architecture implémentée (spec §3) : ORCE (Node-RED) en gate devant PostgreSQL —
jamais de SQL direct des sources. Les sources (simulation `POST /ingest/sim` + tick 60 s,
SFTP cycle 60 s, pont MQTT QoS par route) passent par les fonctions gate
(`public.gate_ingest` / `gate_reject`, appelées avec le rôle `gate`) qui valident
l'enveloppe (champs obligatoires, types, bornes, taille → OK/DLQ) puis insèrent en
`bronze.*` (JSONB + lineage, décision D-2). Routage sur le champ `type` de
l'enveloppe (décision D-1). `silver.*` = vues typées (CAST + filtres qualité),
`gold.*` = matviews rafraîchies par pg_cron (*/10 min), watermarks dans
`silver._watermarks`. Rôles PG : `gate` (INSERT bronze only), `reader` (lecture gold),
`app` (app.*), `admin`. Catalogue DSP auto-dérivé (gold ∩ `datasets.json`,
`format: postgres`) ; data plane `POST /dsp/data` (HMAC D-3, rate limit 10/min/offre,
audit `dsp_access_audit`, `SELECT` + from/to + LIMIT 5000, erreurs JSON-LD
`dspace:Error`). IdP : Keycloak 26.x (décision D-4). caddy en TLS frontal.

## Critères d'acceptation couverts (spec §10)

| CA | Énoncé (résumé) | Suite de test |
|----|-----------------|---------------|
| CA-S1 | Donnée simulation `sim.*` : bronze → silver (≤ 10 min) → gold, lineage complète | `tck/harness/suites/test_ca_s1_ingestion_simulation.py` |
| CA-S2 | Fichier SFTP ingéré (cycle 60 s), archivé, curseur ; fichier invalide → DLQ (`error_type`/`message`/`ts`) | `tck/harness/suites/test_ca_s2_ingestion_sftp.py` |
| CA-S3 | Catalogue DSP = exactement les assets gold déclarés, avec `policySummary` ; non déclaré = absent | `tck/harness/suites/test_ca_s3_catalogue_dsp.py` |
| CA-S4 | Data plane : `{assetId, schema, table, columns, rows, rowCount}`, fenêtre from/to, LIMIT 5000 ; sans contrat → 401/403 `dspace:Error` | `tck/harness/suites/test_ca_s4_data_plane.py` |
| CA-S5 | Rate limit 10 req/min par offre ; accès audités (`agreement_id`) | `tck/harness/suites/test_ca_s5_rate_limit_audit.py` |
| CA-S6 | Watermark silver avance ; `--full-refresh` rejoue tout le silver depuis bronze | `tck/harness/suites/test_ca_s6_watermarks_full_refresh.py` |
| CA-S7 | Rôles PG : `gate` sans lecture, `reader` sans écriture | `tck/harness/suites/test_ca_s7_roles_pg.py` |
| CA-S8 | Dashboards Superset gold sur Postgres sans modification des requêtes | `tck/harness/suites/test_ca_s8_superset_gold.py` |

## Démarrage rapide du TCK

```bash
cd tck
cp tck.properties tck.properties.local   # adapter la cible
./run-tck.sh --properties tck.properties.local
# evidence : tck/evidence/tck-run-<stamp>.log + junit-<stamp>.xml
```

Voir `tck/README.md` pour les prérequis, la configuration détaillée et la portée exacte de chaque test.

> Note : le parser de `tck.properties` garde les commentaires inline (`cle=valeur # comment`) —
> surcharger les clés concernées par des variables d'env `TCK_*` (ex. `TCK_DATA_LIMIT=5000`) plutôt
> que d'éditer le fichier.

## Validation de référence (exécution réelle, 2026-11-19)

Stack déployée en local (docker : postgres16+pg_cron, orce, mosquitto, sftp, keycloak, caddy),
TCK exécuté de bout en bout : **33 tests, 32 OK, 0 échec, 0 erreur, 1 skip** (CA-S8 test API
Superset live, skippé tant que `superset.base.url` n'est pas renseigné — par conception).
Journal : `tck/evidence/tck-run-20261119T201944Z.log` + `junit-20261119T201944Z.xml`.

Note CA-S2 test_03 : le fixture `sftp_invalid.json.b64` est en base64 (la substitution du
marqueur y est un no-op), donc le contenu déposé ne contient jamais le marqueur. Corrigé côté
implémentation : le payload DLQ des rejets fichier est un JSON auto-descriptif
`{"file_name", "raw_payload"}` (brut conservé verbatim), ce qui rend l'entrée DLQ traçable au
fichier — l'assertion `fm in payload` passe via le nom de fichier.
