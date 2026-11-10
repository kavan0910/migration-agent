"""Request-scoped dependencies: db session, rate limiting, current user."""
from __future__ import annotations

from typing import Iterator

from fastapi import Depends, Request
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .db import set_rls_context
from .errors import ApiError
from .models import Membership, User
from .security import AuthError, Identity


def get_db(request: Request) -> Iterator[Session]:
    s = request.app.state.db.sessionmaker()
    try:
        yield s
    finally:
        s.rollback()  # anything not committed explicitly by the handler is discarded
        s.close()


def client_ip(request: Request) -> str:
    st = request.app.state.settings
    peer = request.client.host if request.client else "unknown"
    if st.trust_proxy_headers:
        # X-Forwarded-For is "client, proxy1, proxy2": each trusted proxy appends the address it saw. With N trusted
        # proxies the client is the Nth entry from the right; anything further left is attacker-supplied. If the
        # header has fewer entries than proxies we expected, it did not come through the expected chain: use the peer.
        parts = [p.strip() for p in request.headers.get("x-forwarded-for", "").split(",") if p.strip()]
        n = max(1, st.trusted_proxy_count)
        if len(parts) >= n:
            return parts[-n][:64]
    return peer


def throttle(request: Request) -> None:
    st = request.app.state.settings
    if not st.rate_limit_enabled:
        return
    auth_route = request.url.path.startswith("/v1/auth/")
    bucket = request.app.state.auth_bucket if auth_route else request.app.state.ip_bucket
    ok, retry = bucket.allow(client_ip(request))
    if not ok:
        raise ApiError(429, "rate_limited", "too many requests", {"Retry-After": str(int(retry) + 1)})


def _bearer(request: Request) -> str:
    h = request.headers.get("authorization", "")
    scheme, _, tok = h.partition(" ")
    if scheme.lower() != "bearer" or not tok.strip():
        raise ApiError(401, "unauthorized", "missing or invalid credentials", {"WWW-Authenticate": "Bearer"})
    return tok.strip()


def _resolve_user(db: Session, ident: Identity, mode: str, request: Request) -> User:
    if mode == "dev":
        u = db.get(User, ident.subject)
        if u is None:
            raise ApiError(401, "unauthorized", "unknown user")
        return u
    ext = ident.external_id()
    u = db.scalar(select(User).where(User.external_id == ext))
    if u is None:
        from .services import accounts
        st = request.app.state.settings
        try:
            u = accounts.create_oidc_user(db, ident, plan=st.default_plan, max_workspaces=st.max_workspaces_per_user)
            db.commit()
        except IntegrityError:  # concurrent first login of the same identity: the other request won, use its row
            db.rollback()
            u = db.scalar(select(User).where(User.external_id == ext))
            if u is None:
                raise ApiError(409, "conflict", "account creation conflict, retry") from None
    return u


def current_user(request: Request, db: Session = Depends(get_db)) -> User:
    tok = _bearer(request)
    verifier = request.app.state.verifier
    try:
        ident = verifier.verify(tok)
    except AuthError:
        raise ApiError(401, "unauthorized", "invalid or expired token", {"WWW-Authenticate": "Bearer"}) from None
    user = _resolve_user(db, ident, verifier.mode, request)
    if not user.is_active:
        raise ApiError(401, "unauthorized", "account disabled")
    st = request.app.state.settings
    if st.rate_limit_enabled:
        ok, retry = request.app.state.user_bucket.allow(user.id)
        if not ok:
            raise ApiError(429, "rate_limited", "too many requests", {"Retry-After": str(int(retry) + 1)})
    request.scope.setdefault("state", {})["user_id"] = user.id
    if db.get_bind().dialect.name == "postgresql":
        ids = list(db.scalars(select(Membership.workspace_id).where(Membership.user_id == user.id)))
        set_rls_context(db, ids)
    return user
