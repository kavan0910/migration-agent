import time

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi.testclient import TestClient

from app import security
from app.config import Settings
from app.main import create_app
from app.security import OidcTokens
from tests.conftest import User


def test_signup_login_me(client):
    r = client.post("/v1/auth/signup", json={"email": "A@Example.com", "password": "longenough-pass", "display_name": "Al"})
    assert r.status_code == 201
    assert r.json()["user"]["email"] == "a@example.com"
    r = client.post("/v1/auth/login", json={"email": "a@example.com", "password": "longenough-pass"})
    assert r.status_code == 200
    me = client.get("/v1/me", headers={"Authorization": f"Bearer {r.json()['access_token']}"}).json()
    assert me["email"] == "a@example.com"
    assert me["workspaces"][0]["role"] == "owner"


def test_password_is_hashed_with_argon2(client, db):
    from sqlalchemy import select
    from app.models import User as U
    client.post("/v1/auth/signup", json={"email": "h@example.com", "password": "longenough-pass"})
    with db.session() as s:
        h = s.scalar(select(U.password_hash).where(U.email == "h@example.com"))
    assert h.startswith("$argon2") and "longenough" not in h


def test_bad_credentials_and_duplicates(client):
    client.post("/v1/auth/signup", json={"email": "a@example.com", "password": "longenough-pass"})
    assert client.post("/v1/auth/signup", json={"email": "a@example.com", "password": "longenough-pass"}).status_code == 409
    assert client.post("/v1/auth/login", json={"email": "a@example.com", "password": "wrong-password"}).status_code == 401
    r = client.post("/v1/auth/login", json={"email": "nobody@example.com", "password": "wrong-password"})
    assert r.status_code == 401 and r.json()["error"]["code"] == "unauthorized"
    assert client.post("/v1/auth/signup", json={"email": "b@example.com", "password": "short"}).status_code == 422


def test_requires_token(client):
    for h in ({}, {"Authorization": "Bearer garbage"}, {"Authorization": "Basic abc"}):
        r = client.get("/v1/me", headers=h)
        assert r.status_code == 401 and "error" in r.json()


def test_expired_and_forged_tokens(client, settings):
    u = User(client, "a@example.com")
    now = int(time.time())
    expired = jwt.encode({"sub": u.id, "iss": settings.jwt_issuer, "iat": now - 100, "exp": now - 10}, "x" * 48, algorithm="HS256")
    forged = jwt.encode({"sub": u.id, "iss": settings.jwt_issuer, "exp": now + 100}, "y" * 48, algorithm="HS256")
    none_alg = jwt.encode({"sub": u.id, "iss": settings.jwt_issuer, "exp": now + 100}, key=None, algorithm="none")
    wrong_iss = jwt.encode({"sub": u.id, "iss": "evil", "exp": now + 100}, "x" * 48, algorithm="HS256")
    for t in (expired, forged, none_alg, wrong_iss):
        assert client.get("/v1/me", headers={"Authorization": f"Bearer {t}"}).status_code == 401


def test_signup_disabled(settings, db, store):
    s = settings.model_copy(update={"signup_enabled": False})
    with TestClient(create_app(s, db=db, store=store)) as c:
        assert c.post("/v1/auth/signup", json={"email": "a@example.com", "password": "longenough-pass"}).status_code == 403


def test_rate_limit_on_auth(settings, db, store):
    s = settings.model_copy(update={"rate_limit_enabled": True, "rate_limit_auth_per_minute": 3})
    with TestClient(create_app(s, db=db, store=store)) as c:
        codes = [c.post("/v1/auth/login", json={"email": "a@example.com", "password": "x"}).status_code for _ in range(6)]
    assert codes[:3] == [401, 401, 401] and 429 in codes[3:]


def test_rate_limit_per_user_and_headers(settings, db, store):
    s = settings.model_copy(update={"rate_limit_enabled": True, "rate_limit_per_minute": 5})
    with TestClient(create_app(s, db=db, store=store)) as c:
        u = User(c, "a@example.com")
        codes = [u.get("/v1/me").status_code for _ in range(8)]
        r = u.get("/v1/me")
    assert 429 in codes and r.status_code == 429 and "retry-after" in r.headers
    assert r.json()["error"]["code"] == "rate_limited"


def test_token_bucket_refill():
    from app.ratelimit import TokenBucket
    t = [0.0]
    b = TokenBucket(60, burst=2, clock=lambda: t[0])
    assert b.allow("k")[0] and b.allow("k")[0] and not b.allow("k")[0]
    t[0] += 1.0
    assert b.allow("k")[0]


def test_request_id_header(client):
    r = client.get("/healthz")
    assert r.status_code == 200 and len(r.headers["x-request-id"]) >= 8
    r = client.get("/healthz", headers={"X-Request-Id": "abc-123"})
    assert r.headers["x-request-id"] == "abc-123"
    r = client.get("/healthz", headers={"X-Request-Id": "bad id with spaces\n"})
    assert r.headers["x-request-id"] != "bad id with spaces"
    assert client.get("/readyz").json() == {"status": "ready"}


def test_unknown_route_and_validation_error_shape(client, alice):
    r = client.get("/v1/nope")
    assert r.status_code == 404 and r.json()["error"]["code"] == "not_found"
    r = alice.post(f"/v1/workspaces", json={})
    assert r.status_code == 422 and r.json()["error"]["code"] == "invalid_request"


# ------------------------------------------------------------------------------------------------ OIDC
class _FakeJwks:
    def __init__(self, key):
        self.key = key

    def get_signing_key_from_jwt(self, token):
        class K:  # noqa
            pass
        k = K()
        k.key = self.key.public_key()
        return k


@pytest.fixture
def oidc_app(settings, db, store):
    priv = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    s = settings.model_copy(update={"auth_mode": "oidc", "oidc_issuer": "https://idp.example", "oidc_audience": "ma-api",
                                    "oidc_jwks_url": "https://idp.example/jwks"})
    v = OidcTokens(s, jwk_client=_FakeJwks(priv))
    app = create_app(s, db=db, store=store, verifier=v)
    return app, priv, s


def _tok(priv, **over):
    now = int(time.time())
    c = {"sub": "idp|123", "iss": "https://idp.example", "aud": "ma-api", "exp": now + 300, "email": "o@example.com",
         "email_verified": True, "name": "Oidc User"}
    c.update(over)
    return jwt.encode(c, priv, algorithm="RS256")


def test_oidc_creates_user_on_first_sight(oidc_app):
    app, priv, _ = oidc_app
    with TestClient(app) as c:
        h = {"Authorization": f"Bearer {_tok(priv)}"}
        me = c.get("/v1/me", headers=h).json()
        assert me["email"] == "o@example.com" and len(me["workspaces"]) == 1
        me2 = c.get("/v1/me", headers=h).json()
        assert me2["id"] == me["id"]  # no duplicate
        assert c.post("/v1/auth/login", json={"email": "o@example.com", "password": "x"}).status_code == 404
        assert c.post("/v1/auth/signup", json={"email": "z@example.com", "password": "longenough-pass"}).status_code == 404


def test_oidc_rejects_bad_tokens(oidc_app):
    app, priv, _ = oidc_app
    other = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    bad = [_tok(priv, aud="other"), _tok(priv, iss="https://evil"), _tok(priv, exp=int(time.time()) - 5),
           _tok(other), jwt.encode({"sub": "x", "iss": "https://idp.example", "aud": "ma-api", "exp": int(time.time()) + 99}, "secret" * 8, algorithm="HS256")]
    with TestClient(app) as c:
        for t in bad:
            assert c.get("/v1/me", headers={"Authorization": f"Bearer {t}"}).status_code == 401


def test_oidc_unverified_email_not_linked(oidc_app, db):
    app, priv, _ = oidc_app
    with TestClient(app) as c:
        c.get("/v1/me", headers={"Authorization": f"Bearer {_tok(priv)}"})
        r = c.get("/v1/me", headers={"Authorization": f"Bearer {_tok(priv, sub='idp|evil', email_verified=False)}"}).json()
    assert r["email"].endswith("@oidc.invalid")  # attacker does not get the victim's email identity


def test_oidc_requires_config_and_asymmetric(settings):
    with pytest.raises(RuntimeError):
        OidcTokens(settings.model_copy(update={"oidc_issuer": None}))
    with pytest.raises(RuntimeError):
        OidcTokens(settings.model_copy(update={"oidc_issuer": "i", "oidc_audience": "a", "oidc_jwks_url": "u",
                                               "oidc_algorithms": "HS256,none"}))


def test_production_requires_jwt_secret():
    with pytest.raises(RuntimeError):
        Settings(environment="production", jwt_secret=None).effective_jwt_secret()


def test_verify_password_unknown_user_constant_path():
    assert security.verify_password("x", None) is False
