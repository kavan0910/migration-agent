"""init"""
from alembic import op
import sqlalchemy as sa


def upgrade():
    op.create_table(
        "accounts",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.String(length=50), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.add_column("accounts", sa.Column("created_at", sa.DateTime(), nullable=True))


def downgrade():
    op.drop_table("accounts")
