"""Row-level security on workspace-scoped tables (PostgreSQL only; no-op elsewhere).

Policies allow a row when its workspace_id is in the session setting ``app.workspace_ids`` (set per transaction by
the API from the caller's memberships) or when ``app.bypass`` is 'on' (trusted worker / migrations / admin tasks).
RLS is ENABLED, not FORCED: the table owner bypasses it. Run the API as a non-owner role (see deploy docs) for the
policies to protect against application bugs. NOT exercised by the SQLite test-suite.

Revision ID: 0002
Revises: 0001
"""
from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None

TABLES = ["projects", "sources", "user_docs", "runs", "run_events", "artifacts", "usage_events", "jobs",
          "comments", "resolutions"]
COND = ("(current_setting('app.bypass', true) = 'on' OR workspace_id = ANY "
        "(string_to_array(coalesce(current_setting('app.workspace_ids', true), ''), ',')))")


def upgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return
    for t in TABLES:
        op.execute(f"ALTER TABLE {t} ENABLE ROW LEVEL SECURITY")
        op.execute(f"CREATE POLICY tenant_isolation ON {t} USING {COND} WITH CHECK {COND}")


def downgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return
    for t in TABLES:
        op.execute(f"DROP POLICY IF EXISTS tenant_isolation ON {t}")
        op.execute(f"ALTER TABLE {t} DISABLE ROW LEVEL SECURITY")
