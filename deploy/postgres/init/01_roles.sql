-- ============================================================================
-- FAP-light — 01_roles.sql : les 4 rôles PostgreSQL (spec §C1.4, CA-S7)
--   admin  : superuser (POSTGRES_USER du conteneur), propriétaire de tout
--   gate   : INSERT bronze only (+ EXECUTE sur les fonctions gate SECURITY
--            DEFINER) — JAMAIS de lecture
--   reader : lecture gold uniquement (data plane + catalogue) — JAMAIS d'écriture
--   app    : lecture/écriture app.* uniquement (réservé étape 2)
-- Mots de passe passés en variables psql par init.sh (:'gate_pw' etc.).
-- ============================================================================

DO $$
BEGIN
  IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'gate') THEN
    CREATE ROLE gate LOGIN PASSWORD :'gate_pw';
  ELSE
    ALTER ROLE gate LOGIN PASSWORD :'gate_pw';
  END IF;
  IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'reader') THEN
    CREATE ROLE reader LOGIN PASSWORD :'reader_pw';
  ELSE
    ALTER ROLE reader LOGIN PASSWORD :'reader_pw';
  END IF;
  IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'app') THEN
    CREATE ROLE app LOGIN PASSWORD :'app_pw';
  ELSE
    ALTER ROLE app LOGIN PASSWORD :'app_pw';
  END IF;
END
$$;

-- Le rôle gate ne doit lire AUCUNE table, y compris les vues système
-- (CA-S7 : SELECT 1 FROM pg_roles doit échouer pour gate). pg_roles est
-- lisible par PUBLIC par défaut : on révoque et on re-grante explicitement.
REVOKE SELECT ON pg_catalog.pg_roles FROM PUBLIC;
GRANT SELECT ON pg_catalog.pg_roles TO reader, app;
-- admin est superuser : contourne les vérifications de permission.

-- Droits du gate : INSERT sur bronze uniquement, aucune lecture.
GRANT USAGE ON SCHEMA bronze TO gate;
-- (les GRANT sur les tables bronze sont faits dans 02_core.sql, une fois les
--  tables créées ; idem pour EXECUTE sur les fonctions gate)
