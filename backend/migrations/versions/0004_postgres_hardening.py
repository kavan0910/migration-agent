"""Postgres-only hardening: FORCE row level security, append-only audit_log.

UNVERIFIED: written without a Postgres instance available; the SQLite suite skips it (no-op on other dialects). A
Postgres-marked test (tests/test_pentest_postgres.py, needs TEST_DATABASE_URL) exercises it.

* FORCE ROW LEVEL SECURITY: the table OWNER is subject to the tenant policies as well, so an application that
  (mis)connects as the owner is still confined. (Superusers and roles with BYPASSRLS always bypass: the deploy stack
  therefore runs the API/worker as a plain `ma_app` role, see deploy/postgres-init.)
* audit_log is append-only: a trigger rejects UPDATE/DELETE/TRUNCATE, and when the `ma_app` role exists its
  UPDATE/DELETE/TRUNCATE privileges are revoked.

Revision ID: 0004
Revises: 0003
"""
from alembic import op

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None

TABLES = ["projects", "sources", "user_docs", "runs", "run_events", "artifacts", "usage_events", "jobs",
          "comments", "resolutions"]


def upgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return
    for t in TABLES:
        op.execute(f"ALTER TABLE {t} FORCE ROW LEVEL SECURITY")
    op.execute("""
        CREATE OR REPLACE FUNCTION audit_log_append_only() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
          RAISE EXCEPTION 'audit_log is append-only' USING ERRCODE = 'insufficient_privilege';
        END $$""")
    op.execute("DROP TRIGGER IF EXISTS audit_log_no_update_delete ON audit_log")
    op.execute("CREATE TRIGGER audit_log_no_update_delete BEFORE UPDATE OR DELETE ON audit_log "
               "FOR EACH ROW EXECUTE FUNCTION audit_log_append_only()")
    op.execute("DROP TRIGGER IF EXISTS audit_log_no_truncate ON audit_log")
    op.execute("CREATE TRIGGER audit_log_no_truncate BEFORE TRUNCATE ON audit_log "
               "FOR EACH STATEMENT EXECUTE FUNCTION audit_log_append_only()")
    op.execute("""
        DO $$ BEGIN
          IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'ma_app') THEN
            REVOKE UPDATE, DELETE, TRUNCATE ON audit_log FROM ma_app;
          END IF;
        END $$""")


def downgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return
    op.execute("DROP TRIGGER IF EXISTS audit_log_no_truncate ON audit_log")
    op.execute("DROP TRIGGER IF EXISTS audit_log_no_update_delete ON audit_log")
    op.execute("DROP FUNCTION IF EXISTS audit_log_append_only()")
    for t in TABLES:
        op.execute(f"ALTER TABLE {t} NO FORCE ROW LEVEL SECURITY")
