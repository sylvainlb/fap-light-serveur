-- ============================================================================
-- FAP-light — 01_roles.sql : the 4 PostgreSQL roles (spec §C1.4, CA-S7)
--   admin  : superuser (container POSTGRES_USER), owns everything
--   gate   : INSERT bronze only (+ EXECUTE on the gate functions) — NEVER any read
--   reader : gold read only (data plane + catalog) — NEVER any write
--   app    : read/write on app.* only (reserved for step 2)
-- Passwords passed as psql variables by init.sh (:'gate_pw' etc.).
-- Note: no DO $$ block here — psql does not interpolate :'var' inside
-- dollar-quotes ; we go through SELECT ... \gexec + ALTER ROLE (idempotent).
-- ============================================================================

SELECT format('CREATE ROLE gate LOGIN PASSWORD %L', :'gate_pw')
WHERE NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'gate') \gexec
SELECT format('CREATE ROLE reader LOGIN PASSWORD %L', :'reader_pw')
WHERE NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'reader') \gexec
SELECT format('CREATE ROLE app LOGIN PASSWORD %L', :'app_pw')
WHERE NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'app') \gexec

ALTER ROLE gate   LOGIN PASSWORD :'gate_pw';
ALTER ROLE reader LOGIN PASSWORD :'reader_pw';
ALTER ROLE app    LOGIN PASSWORD :'app_pw';

-- The gate role must not read ANY table, including system views
-- (CA-S7: SELECT 1 FROM pg_roles must fail for gate). pg_roles is
-- readable by PUBLIC by default: revoke it and re-grant explicitly.
REVOKE SELECT ON pg_catalog.pg_roles FROM PUBLIC;
GRANT SELECT ON pg_catalog.pg_roles TO reader, app;
-- admin is superuser: bypasses permission checks.

-- Gate rights on the bronze schema (USAGE + INSERT tables + EXECUTE
-- gate functions): done in 02_core.sql, once the schema exists.
