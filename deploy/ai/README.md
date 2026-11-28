# AI stack — workstream 5 (dedicated ORCE service)

Spec: `../../../spec/app-ai-component-editor.md` (rev 5). Dedicated service of the
fap-light-serveur repository, **enabled by configuration** (`COMPOSE_PROFILES=ai` in `.env`),
routed by caddy on `/ai/*` (`deploy/caddy/Caddyfile`). Changes neither the core nor E11,
introduces no table on the data server side (§3.0).

## Delivered state (J15 + J16 — engine, internal API, text AND structured outputs)

- `POST /ai/run` (annex C): `component` (published) XOR `componentDraft` (editor, counted
  as test), `source`, `locale` (default `en`). Stable codes: 200/202/400/401/403/404/
  422 `component_invalid` /429 `budget_exceeded` /502 `model_call_failed` (no write, F2.3).
- Text outputs (F3.1/F3.2): `perResource` = PATCH of `cmp:outputField` on the source
  resource (synchronous, 200); `perContainer` = one run over the batch (R1.5, placeholders
  instantiated with the list of batch values), autonomous document POSTed into
  `cmp:outputTarget` with `source_container` + provenance (asynchronous, 202 + job).
- `GET /ai/jobs/<id>`: tracking resource, **requester only** (403 otherwise), service-local
  storage (volume `ai-data`), retention `AI_JOB_RETENTION_DAYS` (default 7 d).
- `GET /ai/models`: registry hot-reloaded from `/ai_models/` (annex B).

**Provisioning**: the `app.ai_models` table (container `/ai_models/`, pk `name`) is
declared in the deployment's `config/model.json` (applied at orce boot);
ACL + local model seed in `app-supervision-backend/sql/142_components_ai_models_acl.sql`
(read foaf:Agent, write admin — no credential, F1.4).
- `GET /ai/admin/counters`: run counters per component/model (F5.3), admin role.
- Provenance (R1.8): 5 columns of the written table filled at write time (model,
  component, prompt version, date, locale).
- Budget (F6.3): `AI_BUDGET_MAX_RUNS` per component over `AI_BUDGET_WINDOW_DAYS` → 429.
- Component document schema: `schema/ai-component.schema.json`, extracted verbatim from
  annex A of the spec (single source of truth).

- **J16 delivered**: structured outputs — constrained generation (native response_format if
  `native_structured_output`, else JSON schema in the prompt + parsing), JSON-LD conversion
  (sh:path IRIs), SHACL validation via the J3 ext/shacl endpoint (W3C report), retries with
  counter-argumentation (F4.3), POST write on the standard path (F4.4). Non-conformant after
  retries → `422 output_validation_failed` + `validationReport`, no write.

## Not delivered (J19 and +)

- Identity propagation (F6.2, arbitrated J15 rev 6): **RFC 8693 token exchange by default**
  (`AI_TOKEN_EXCHANGE=1`, `IDP_TOKEN_URL`, `AI_CLIENT_ID`, `AI_CLIENT_SECRET`); dev fallback
  `AI_TOKEN_EXCHANGE=0` forwards the Bearer (`AI_JWT_VERIFY=0` decodes without signature
  verification, `AI_DEV_INSECURE=1` accepts `X-Webid`/`X-Role`).
- Iteration 2 (J19): federated FACIS catalog, AI components in the WYSIWYG, provenance views.

## Local development

```bash
cd deploy/ai
python3 -m unittest discover -s tests -v   # 40 tests (schema, template, registry, text engine
                                            # + structured J16, identity F6.2, API)
python3 -m app.server                        # with LDP_BASE_URL, AI_MODELS_CONTAINER, ...
```

## Deployment

```bash
# .env : COMPOSE_PROFILES=ai  (+ LDP_BASE_URL, AI_MODEL_SECRET_<MODEL>=... for remote ones, F1.4)
./scripts/deploy.sh
# caddy routes /ai/* -> ai:8080 ; the rest of the traffic is unchanged
```
