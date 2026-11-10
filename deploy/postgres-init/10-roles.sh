#!/bin/sh
# Runs once, as the bootstrap superuser (POSTGRES_USER), when the data directory is first initialised.
# Creates two NON-superuser roles so row-level security actually applies:
#   ma_owner : owns the schema; used ONLY by the one-shot `migrate` service (DDL).
#   ma_app   : what the api and worker connect as; DML only, no DDL, subject to RLS (NOBYPASSRLS).
# UNVERIFIED: written without Postgres or Docker available on the authoring machine.
set -eu
: "${MA_OWNER_PASSWORD:?}" "${MA_APP_PASSWORD:?}"
psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" \
     -v owner_pw="$MA_OWNER_PASSWORD" -v app_pw="$MA_APP_PASSWORD" -v db="$POSTGRES_DB" <<'SQL'
CREATE ROLE ma_owner LOGIN PASSWORD :'owner_pw' NOSUPERUSER NOCREATEDB NOCREATEROLE NOBYPASSRLS;
CREATE ROLE ma_app   LOGIN PASSWORD :'app_pw'   NOSUPERUSER NOCREATEDB NOCREATEROLE NOBYPASSRLS;
ALTER DATABASE :"db" OWNER TO ma_owner;
REVOKE ALL ON DATABASE :"db" FROM PUBLIC;
GRANT CONNECT ON DATABASE :"db" TO ma_owner, ma_app;
ALTER SCHEMA public OWNER TO ma_owner;
REVOKE CREATE ON SCHEMA public FROM PUBLIC;
GRANT USAGE ON SCHEMA public TO ma_app;
ALTER DEFAULT PRIVILEGES FOR ROLE ma_owner IN SCHEMA public GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO ma_app;
ALTER DEFAULT PRIVILEGES FOR ROLE ma_owner IN SCHEMA public GRANT USAGE, SELECT ON SEQUENCES TO ma_app;
SQL
