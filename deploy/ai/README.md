# Pile IA — chantier 5 (service dédié ORCE)

Spec : `../../../spec/app-ai-component-editor.md` (rev 5). Service dédié du dépôt
fap-light-serveur, **activé par configuration** (`COMPOSE_PROFILES=ai` dans `.env`),
routé par caddy sur `/ai/*` (`deploy/caddy/Caddyfile`). Ne modifie ni le cœur ni E11,
n'introduit aucune table côté serveur de données (§3.0).

## État livré (J15 + J16 — moteur, API interne, sorties texte ET structurées)

- `POST /ai/run` (annexe C) : `component` (publié) XOR `componentDraft` (éditeur, compté
  comme test), `source`, `locale` (défaut `en`). Codes stables : 200/202/400/401/403/404/
  422 `component_invalid` /429 `budget_exceeded` /502 `model_call_failed` (aucune écriture, F2.3).
- Sorties texte (F3.1/F3.2) : `perResource` = PATCH de `cmp:outputField` sur la ressource
  source (synchrone, 200) ; `perContainer` = une exécution sur le lot (R1.5, placeholders
  instanciés avec la liste des valeurs du lot), document autonome POSTé dans
  `cmp:outputTarget` avec `source_container` + provenance (asynchrone, 202 + job).
- `GET /ai/jobs/<id>` : ressource de suivi, **demandeur seul** (403 sinon), stockage propre
  du service (volume `ai-data`), rétention `AI_JOB_RETENTION_DAYS` (défaut 7 j).
- `GET /ai/models` : registre rechargé à la volée depuis `/ai_models/` (annexe B).

**Provisionnement** : la table `app.ai_models` (conteneur `/ai_models/`, pk `name`) est
déclarée dans le `config/model.json` du déploiement (appliquée au boot d'orce) ;
ACL + seed du modèle local dans `app-supervision-backend/sql/142_components_ai_models_acl.sql`
(read foaf:Agent, write admin — aucun credential, F1.4).
- `GET /ai/admin/counters` : compteurs de runs par composant/modèle (F5.3), rôle admin.
- Provenance (R1.8) : 5 colonnes de la table écrite remplies à l'écriture (modèle,
  composant, version de prompt, date, locale).
- Budget (F6.3) : `AI_BUDGET_MAX_RUNS` par composant sur `AI_BUDGET_WINDOW_DAYS` → 429.
- Schéma du document composant : `schema/ai-component.schema.json`, extrait tel quel de
  l'annexe A de la spec (source de vérité unique).

- **J16 livré** : sorties structurées — génération contrainte (response_format natif si
  `native_structured_output`, sinon schéma JSON dans le prompt + parsing), conversion JSON-LD
  (IRIs des sh:path), validation SHACL via l'endpoint J3 ext/shacl (rapport W3C), retries avec
  contre-argumentation (F4.3), écriture POST voie standard (F4.4). Non conforme après
  tentatives → `422 output_validation_failed` + `validationReport`, aucune écriture.

## Non livré (J19 et +)

- Propagation d'identité (F6.2, arbitré J15 rev 6) : **token exchange RFC 8693 par défaut**
  (`AI_TOKEN_EXCHANGE=1`, `IDP_TOKEN_URL`, `AI_CLIENT_ID`, `AI_CLIENT_SECRET`) ; repli dev
  `AI_TOKEN_EXCHANGE=0` forward le Bearer (`AI_JWT_VERIFY=0` décode sans vérif de signature,
  `AI_DEV_INSECURE=1` accepte `X-Webid`/`X-Role`).
- Itération 2 (J19) : catalogue fédéré FACIS, composants IA dans le WYSIWYG, vues de provenance.

## Développement local

```bash
cd deploy/ai
python3 -m unittest discover -s tests -v   # 40 tests (schéma, template, registre, moteur texte
                                            # + structuré J16, identité F6.2, API)
python3 -m app.server                        # avec LDP_BASE_URL, AI_MODELS_CONTAINER, ...
```

## Déploiement

```bash
# .env : COMPOSE_PROFILES=ai  (+ LDP_BASE_URL, AI_MODEL_SECRET_<MODELE>=... pour les distants, F1.4)
./scripts/deploy.sh
# caddy route /ai/* -> ai:8080 ; le reste du trafic est inchangé
```
