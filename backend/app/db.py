"""Engine/session management. SQLite for dev/tests, Postgres (psycopg) in production."""
from __future__ import annotations

from contextlib import contextmanager
from typing import Iterator

from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker


class Base(DeclarativeBase):
    pass


def normalise_url(url: str) -> str:
    if url.startswith("postgres://"):
        url = "postgresql://" + url[len("postgres://"):]
    if url.startswith("postgresql://"):
        url = "postgresql+psycopg://" + url[len("postgresql://"):]
    return url


def make_engine(url: str) -> Engine:
    url = normalise_url(url)
    if url.startswith("sqlite"):
        eng = create_engine(url, connect_args={"check_same_thread": False, "timeout": 30}, future=True)

        @event.listens_for(eng, "connect")
        def _pragmas(dbapi_conn, _):  # pragma: no cover - trivial
            cur = dbapi_conn.cursor()
            cur.execute("PRAGMA foreign_keys=ON")
            cur.execute("PRAGMA journal_mode=WAL")
            cur.execute("PRAGMA busy_timeout=30000")
            cur.close()
        return eng
    return create_engine(url, pool_pre_ping=True, future=True)


class Database:
    def __init__(self, url: str):
        self.url = normalise_url(url)
        self.engine = make_engine(self.url)
        self.sessionmaker = sessionmaker(self.engine, expire_on_commit=False, class_=Session)
        self.bypass_rls = False
        if self.engine.dialect.name == "postgresql":
            event.listen(self.sessionmaker, "after_begin", self._after_begin)

    def _after_begin(self, session, transaction, connection) -> None:  # pragma: no cover - needs Postgres
        from sqlalchemy import text
        info = session.info
        connection.execute(text("SELECT set_config('app.workspace_ids', :v, true), set_config('app.bypass', :b, true)"),
                           {"v": ",".join(info.get("rls_ids", [])),
                            "b": "on" if info.get("bypass", self.bypass_rls) else "off"})

    @property
    def is_postgres(self) -> bool:
        return self.engine.dialect.name == "postgresql"

    @contextmanager
    def session(self) -> Iterator[Session]:
        s = self.sessionmaker()
        try:
            yield s
            s.commit()
        except Exception:
            s.rollback()
            raise
        finally:
            s.close()

    def create_all(self) -> None:
        from . import models  # noqa: F401
        Base.metadata.create_all(self.engine)

    def dispose(self) -> None:
        self.engine.dispose()


def set_rls_context(session: Session, workspace_ids: list[str] | None, bypass: bool = False) -> None:
    """Postgres only: expose the caller's workspaces to row-level security policies for this transaction."""
    if session.get_bind().dialect.name != "postgresql":
        return
    from sqlalchemy import text
    session.info["rls_ids"], session.info["bypass"] = list(workspace_ids or []), bypass
    session.execute(text("SELECT set_config('app.workspace_ids', :v, true), set_config('app.bypass', :b, true)"),
                    {"v": ",".join(workspace_ids or []), "b": "on" if bypass else "off"})
