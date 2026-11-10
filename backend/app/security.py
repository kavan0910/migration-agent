"""Authentication primitives. No hand-rolled crypto: argon2-cffi for passwords, PyJWT for tokens (HS256 for the dev
issuer, asymmetric algorithms only for externally issued OIDC tokens, keys fetched from the issuer's JWKS)."""
from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any, Protocol

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError

from .config import Settings


class AuthError(Exception):
    pass


@dataclass
class Identity:
    subject: str
    email: str | None = None
    name: str | None = None
    email_verified: bool = False
    issuer: str | None = None

    def external_id(self) -> str:
        """Stable local key for an externally issued identity: scoped by issuer, so the same ``sub`` from a different
        issuer (or after an issuer change) can never resolve to an existing account."""
        return f"{self.issuer}|{self.subject}" if self.issuer else self.subject


_ph = PasswordHasher()
_DUMMY_HASH = _ph.hash("not-a-real-password-for-timing")


def hash_password(pw: str) -> str:
    return _ph.hash(pw)


def verify_password(pw: str, stored: str | None) -> bool:
    """Always performs one hash verification so unknown users cost the same as wrong passwords."""
    try:
        _ph.verify(stored or _DUMMY_HASH, pw)
        return stored is not None
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        return False


class TokenVerifier(Protocol):
    mode: str

    def verify(self, token: str) -> Identity: ...


class DevTokens:
    """Local JWT issuer/verifier for AUTH_MODE=dev."""
    mode = "dev"

    def __init__(self, settings: Settings):
        self.secret = settings.effective_jwt_secret()
        self.ttl = settings.jwt_ttl_minutes * 60
        self.issuer = settings.jwt_issuer

    def issue(self, user_id: str) -> tuple[str, int]:
        now = int(time.time())
        tok = jwt.encode({"sub": user_id, "iss": self.issuer, "iat": now, "exp": now + self.ttl}, self.secret,
                         algorithm="HS256")
        return tok, self.ttl

    def verify(self, token: str) -> Identity:
        try:
            c = jwt.decode(token, self.secret, algorithms=["HS256"], issuer=self.issuer,
                           options={"require": ["exp", "sub", "iss"]})
        except jwt.PyJWTError:
            raise AuthError("invalid token") from None
        return Identity(subject=str(c["sub"]))


class OidcTokens:
    """Verifies externally issued JWTs against the issuer's JWKS. ``jwk_client`` is injectable for tests."""
    mode = "oidc"

    def __init__(self, settings: Settings, jwk_client: Any | None = None):
        if not (settings.oidc_issuer and settings.oidc_audience and (settings.oidc_jwks_url or jwk_client)):
            raise RuntimeError("OIDC_ISSUER, OIDC_AUDIENCE and OIDC_JWKS_URL are required when AUTH_MODE=oidc")
        self.issuer, self.audience = settings.oidc_issuer, settings.oidc_audience
        algs = [a.strip() for a in settings.oidc_algorithms.split(",") if a.strip()]
        self.algorithms = [a for a in algs if not a.upper().startswith("HS") and a.lower() != "none"]
        if not self.algorithms:
            raise RuntimeError("OIDC_ALGORITHMS must contain an asymmetric algorithm")
        self.jwks = jwk_client or jwt.PyJWKClient(settings.oidc_jwks_url, cache_keys=True, lifespan=3600)

    def verify(self, token: str) -> Identity:
        try:
            key = self.jwks.get_signing_key_from_jwt(token).key
            c = jwt.decode(token, key, algorithms=self.algorithms, audience=self.audience, issuer=self.issuer,
                           options={"require": ["exp", "sub", "iss"]})
        except (jwt.PyJWTError, jwt.PyJWKClientError):
            raise AuthError("invalid token") from None
        return Identity(subject=str(c["sub"]), email=c.get("email"), name=c.get("name"),
                        email_verified=bool(c.get("email_verified")), issuer=self.issuer)


def make_verifier(settings: Settings) -> TokenVerifier:
    return OidcTokens(settings) if settings.auth_mode == "oidc" else DevTokens(settings)
