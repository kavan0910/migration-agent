"""Server configuration, read from the environment (never from a project)."""
from __future__ import annotations

import os
import secrets
from functools import lru_cache
from typing import Literal

from pydantic import SecretStr, model_validator
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
    # None = decide from the environment: open in dev/test, CLOSED in production (see _resolve_defaults).
    signup_enabled: bool | None = None
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
    archive_timeout_s: float = 120.0     # wall-clock cap for validating/extracting one archive
    max_archive_entries: int = 100_000   # every member, directories included
    max_tree_depth: int = 64

    # rate limiting (in-process token bucket)
    rate_limit_enabled: bool = True
    rate_limit_per_minute: int = 300
    rate_limit_auth_per_minute: int = 20
    trust_proxy_headers: bool = False
    trusted_proxy_count: int = 1         # number of proxies that append to X-Forwarded-For (only if trusted)
    login_fail_threshold: int = 5        # failed logins per account before exponential backoff starts
    login_backoff_base_s: float = 2.0
    login_backoff_max_s: float = 900.0

    # abuse / denial-of-wallet ceilings (configuration, not pricing)
    default_plan: str = "free"           # plan given to self-serve workspaces; "internal" only by explicit assignment
    max_workspaces_per_user: int = 3
    max_user_active_runs: int = 4        # across all workspaces, per creating user
    max_user_monthly_usd: float = 50.0   # spent + reserved, per creating user, across workspaces
    max_user_monthly_runs: int = 100
    invite_ttl_hours: int = 72
    max_pending_invites: int = 50
    estimate_slots_per_workspace: int = 1
    estimate_slots_global: int = 3

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
    sse_max_per_user: int = 5
    sse_max_per_run: int = 20
    sse_max_lifetime_s: float = 3600.0
    sse_recheck_s: float = 10.0          # membership / token-expiry re-validation interval
    reaper_interval_s: float = 30.0      # worker: retry pending object deletions this often
    reconcile_interval_s: float = 900.0  # worker: scan the store for prefixes with no project row
    deletion_retry_base_s: float = 30.0
    deletion_retry_max_s: float = 3600.0

    @model_validator(mode="after")
    def _resolve_defaults(self) -> "Settings":
        if self.signup_enabled is None:
            self.signup_enabled = self.environment != "production"
        # Fail closed: a container image always carries MA_IN_CONTAINER=1; if ENVIRONMENT was not provided
        # explicitly we refuse to start rather than silently run with development defaults.
        if os.environ.get("MA_IN_CONTAINER") == "1" and "environment" not in self.model_fields_set:
            raise ValueError("ENVIRONMENT must be set explicitly inside a container (dev|test|production)")
        return self

    @property
    def cors_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    @property
    def allowed_git_hosts(self) -> set[str]:
        return {h.strip().lower() for h in self.git_allowed_hosts.split(",") if h.strip()}

    def effective_jwt_secret(self) -> str:
        sec = self.jwt_secret.get_secret_value() if self.jwt_secret else ""
        if not sec:
            if self.environment == "production":
                raise RuntimeError("JWT_SECRET must be set in production when AUTH_MODE=dev")
            return _ephemeral_secret()
        if self.environment != "dev":
            _check_secret_strength(sec, strict=self.environment == "production")
        return sec


PLACEHOLDER_MARKERS = ("change-me", "changeme", "change_me", "replace-me", "replaceme", "your-secret", "yoursecret",
                       "example", "placeholder", "password", "secret-key", "default", "insecure", "dev-secret",
                       "generate-with")


def _check_secret_strength(sec: str, strict: bool) -> None:
    if len(sec.encode()) < 32:
        raise RuntimeError("JWT_SECRET must be at least 32 bytes (generate with: openssl rand -hex 32)")
    low = sec.lower()
    if any(m in low for m in PLACEHOLDER_MARKERS):
        raise RuntimeError("JWT_SECRET is a known placeholder value; generate a real one (openssl rand -hex 32)")
    if strict and len(set(sec)) < 8:
        raise RuntimeError("JWT_SECRET has too little entropy; generate with: openssl rand -hex 32")


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
