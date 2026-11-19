-- ============================================================================
-- FAP-light — 01_roles.sql : les 4 rôles PostgreSQL (spec §C1.4, CA-S7)
--   admin  : superuser (POSTGRES_USER du conteneur), propriétaire de tout
--   gate   : INSERT bronze only (+ EXECUTE sur les fonctions gate) — JAMAIS de lecture
--   reader : lecture gold uniquement (data plane + catalogue) — JAMAIS d'écriture
--   app    : lecture/écriture app.* uniquement (réservé étape 2)
-- Mots de passe passés en variables psql par init.sh (:'gate_pw' etc.).
-- Note : pas de bloc DO $$ ici — psql n'interpole pas :'var' dans les
-- dollar-quotes ; on passe par SELECT ... \gexec + ALTER ROLE (idempotent).
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

-- Le rôle gate ne doit lire AUCUNE table, y compris les vues système
-- (CA-S7 : SELECT 1 FROM pg_roles doit échouer pour gate). pg_roles est
-- lisible par PUBLIC par défaut : on révoque et on re-grante explicitement.
REVOKE SELECT ON pg_catalog.pg_roles FROM PUBLIC;
GRANT SELECT ON pg_catalog.pg_roles TO reader, app;
-- admin est superuser : contourne les vérifications de permission.

-- Droits du gate sur le schéma bronze (USAGE + INSERT tables + EXECUTE
-- fonctions gate) : faits dans 02_core.sql, une fois le schéma créé.
