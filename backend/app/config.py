"""Server configuration, read from the environment (never from a project)."""
from __future__ import annotations

import secrets
from functools import lru_cache
from typing import Literal

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=None, extra="ignore", case_sensitive=False)

    environment: Literal["dev", "test", "production"] = "dev"
    database_url: str = "sqlite:///./dev.db"
    log_level: str = "INFO"
    log_json: bool = True
    cors_origins: str = ""  # comma separated

    # auth
    auth_mode: Literal["dev", "oidc"] = "dev"
    signup_enabled: bool = True
    jwt_secret: SecretStr | None = None
    jwt_ttl_minutes: int = 60
    jwt_issuer: str = "migration-agent-dev"
    oidc_issuer: str | None = None
    oidc_audience: str | None = None
    oidc_jwks_url: str | None = None
    oidc_algorithms: str = "RS256,ES256"

    # object store
    store_backend: Literal["local", "s3"] = "local"
    store_dir: str = "./data/store"
    s3_bucket: str | None = None
    s3_endpoint_url: str | None = None
    s3_region: str | None = None

    # ingestion limits
    max_upload_mb: int = 100
    max_extracted_mb: int = 500
    max_file_mb: int = 50
    max_archive_files: int = 50_000
    max_compression_ratio: int = 200
    max_user_doc_mb: int = 10
    max_user_docs: int = 200
    git_timeout_s: int = 120
    git_max_mb: int = 300
    git_allowed_hosts: str = ""  # comma separated allowlist; empty = any public host
    git_binary: str = "git"

    # rate limiting (in-process token bucket)
    rate_limit_enabled: bool = True
    rate_limit_per_minute: int = 300
    rate_limit_auth_per_minute: int = 20
    trust_proxy_headers: bool = False

    # worker
    worker_poll_s: float = 1.0
    job_lock_timeout_s: int = 600
    job_max_attempts: int = 3
    job_backoff_base_s: float = 10.0
    scratch_dir: str | None = None
    engine_offline: bool = False  # run the engine without any model (demo / e2e tests)
    engine_workers: int = 2

    # model access (server side only)
    model_api_key: SecretStr | None = None
    model_api_base: str | None = None
    profile_fast_model: str = "gemini/gemini-2.5-flash-lite"
    profile_balanced_model: str = "gemini/gemini-2.5-flash"
    profile_best_model: str = "gemini/gemini-2.5-pro"
    default_max_usd: float = 5.0
    hard_max_usd: float = 200.0

    sse_poll_s: float = 0.5
    sse_heartbeat_s: float = 15.0

    @property
    def cors_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    @property
    def allowed_git_hosts(self) -> set[str]:
        return {h.strip().lower() for h in self.git_allowed_hosts.split(",") if h.strip()}

    def effective_jwt_secret(self) -> str:
        if self.jwt_secret and self.jwt_secret.get_secret_value():
            return self.jwt_secret.get_secret_value()
        if self.environment == "production":
            raise RuntimeError("JWT_SECRET must be set in production when AUTH_MODE=dev")
        return _ephemeral_secret()


@lru_cache(maxsize=1)
def _ephemeral_secret() -> str:
    return secrets.token_urlsafe(48)


_settings: Settings | None = None


def get_settings() -> Settings:
    global _settings
    if _settings is None:
        _settings = Settings()
    return _settings


def set_settings(s: Settings | None) -> None:
    """Replace the cached settings (tests, app factory)."""
    global _settings
    _settings = s
