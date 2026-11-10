"""model_profile -> engine config. The ONLY place that maps customer-facing profiles to model ids.

Customers choose fast | balanced | best; model ids and the API key come from server configuration (env) and are
injected per job. Nothing here is stored in a project.
"""
from __future__ import annotations

from .config import Settings

PROFILES = ("fast", "balanced", "best")
TASKS = ("chunk_extract", "cross_ref", "business_rules", "gap_fill", "verifier")
# which profile-tier each engine task uses: cheap model for bulk extraction, stronger for reasoning tasks
_TIERS = {
    "fast": dict.fromkeys(TASKS, "fast"),
    "balanced": {"chunk_extract": "fast", "cross_ref": "balanced", "business_rules": "balanced",
                 "gap_fill": "balanced", "verifier": "balanced"},
    "best": {"chunk_extract": "balanced", "cross_ref": "best", "business_rules": "best", "gap_fill": "best",
             "verifier": "best"},
}


def model_for_tier(settings: Settings, tier: str) -> str:
    return {"fast": settings.profile_fast_model, "balanced": settings.profile_balanced_model,
            "best": settings.profile_best_model}[tier]


def engine_config(profile: str, settings: Settings) -> dict:
    """Engine ``config`` dict (see migration_agent.service). Includes the server's API key: never log or persist it."""
    if profile not in _TIERS:
        raise ValueError(f"unknown model_profile {profile!r}")
    models = {t: model_for_tier(settings, tier) for t, tier in _TIERS[profile].items()}
    cfg: dict = {"models": models, "default_model": model_for_tier(settings, "balanced")}
    if settings.model_api_base:
        cfg["api_base"] = settings.model_api_base
    if settings.model_api_key:
        cfg["api_key"] = settings.model_api_key.get_secret_value()
    return cfg


def public_profile_info(settings: Settings) -> dict:
    return {p: {"models": "server-managed"} for p in PROFILES}
