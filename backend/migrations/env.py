import os

from alembic import context
from sqlalchemy import create_engine

from app import models  # noqa: F401
from app.db import Base, normalise_url

target_metadata = Base.metadata


def _url() -> str:
    return normalise_url(os.environ.get("DATABASE_URL") or context.config.get_main_option("sqlalchemy.url")
                         or "sqlite:///./dev.db")


def run_migrations_offline() -> None:
    context.configure(url=_url(), target_metadata=target_metadata, literal_binds=True, render_as_batch=True)
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    engine = create_engine(_url())
    with engine.connect() as conn:
        context.configure(connection=conn, target_metadata=target_metadata,
                          render_as_batch=conn.dialect.name == "sqlite")
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
