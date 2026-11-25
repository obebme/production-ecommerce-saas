-- =============================================================================
-- Atelier Commerce — PostgreSQL 16 Least-Privilege Role Initialization Script
-- =============================================================================
-- Purpose:
--   Separates DDL/schema ownership (`ecom_migrator`, used solely by `migrate` /
--   `alembic` via MIGRATION_DATABASE_URL) from DML-only runtime access
--   (`ecom_app`, used by the FastAPI application via DATABASE_URL).
--
-- Security Model:
--   - No hardcoded passwords are stored in this file.
--   - Supply strong unique passwords at bootstrap time via `psql -v` variables:
--       psql -U postgres -d postgres \
--         -v target_db="ecom_prod" \
--         -v migrator_password="<STRONG_RANDOM_MIGRATOR_PASSWORD>" \
--         -v app_password="<STRONG_RANDOM_APP_PASSWORD>" \
--         -f infra/postgres/init-roles.sql
--
--   - Example inside `compose.prod.yaml` (one-time bootstrap before first migrate):
--       docker compose -f compose.prod.yaml exec -T db \
--         psql -U "$POSTGRES_USER" -d postgres \
--           -v target_db="$POSTGRES_DB" \
--           -v migrator_password="$MIGRATOR_DB_PASSWORD" \
--           -v app_password="$APP_DB_PASSWORD" \
--         < infra/postgres/init-roles.sql
-- =============================================================================

\set ON_ERROR_STOP on

\if :{?target_db}
\else
  \set target_db 'ecom_prod'
\endif

\if :{?migrator_password}
\else
  \echo 'ERROR: Missing -v migrator_password=... argument.'
  \quit 1
\endif

\if :{?app_password}
\else
  \echo 'ERROR: Missing -v app_password=... argument.'
  \quit 1
\endif

-- 1. Create least-privilege DDL owner (`ecom_migrator`) and DML runtime (`ecom_app`)
SELECT format(
    'CREATE ROLE ecom_migrator WITH LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT NOREPLICATION PASSWORD %L',
    :'migrator_password'
)
WHERE NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'ecom_migrator')
\gexec

SELECT format(
    'CREATE ROLE ecom_app WITH LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT NOREPLICATION PASSWORD %L',
    :'app_password'
)
WHERE NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'ecom_app')
\gexec

-- 2. Create target database owned by `ecom_migrator` if not already present
SELECT format(
    'CREATE DATABASE %I OWNER ecom_migrator ENCODING ''UTF8''',
    :'target_db'
)
WHERE NOT EXISTS (SELECT 1 FROM pg_database WHERE datname = :'target_db')
\gexec

SELECT format('ALTER DATABASE %I OWNER TO ecom_migrator', :'target_db')
\gexec

SELECT format('REVOKE ALL ON DATABASE %I FROM PUBLIC', :'target_db')
\gexec

SELECT format('GRANT CONNECT, TEMP ON DATABASE %I TO ecom_migrator', :'target_db')
\gexec

SELECT format('GRANT CONNECT ON DATABASE %I TO ecom_app', :'target_db')
\gexec

-- 3. Configure schema `public` privileges inside the target database
\connect :target_db

REVOKE ALL ON SCHEMA public FROM PUBLIC;
ALTER SCHEMA public OWNER TO ecom_migrator;

GRANT USAGE, CREATE ON SCHEMA public TO ecom_migrator;
GRANT USAGE ON SCHEMA public TO ecom_app;

-- Grant DML-only permissions on any pre-existing tables/sequences
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO ecom_app;
GRANT USAGE ON ALL SEQUENCES IN SCHEMA public TO ecom_app;

-- Ensure future tables and sequences created by `ecom_migrator` (Alembic)
-- automatically grant DML-only access to `ecom_app` without DDL rights.
ALTER DEFAULT PRIVILEGES FOR ROLE ecom_migrator IN SCHEMA public
    GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO ecom_app;

ALTER DEFAULT PRIVILEGES FOR ROLE ecom_migrator IN SCHEMA public
    GRANT USAGE ON SEQUENCES TO ecom_app;

-- Explicitly ensure `ecom_app` has no DDL/object-creation capability on `public`
REVOKE CREATE ON SCHEMA public FROM ecom_app;
