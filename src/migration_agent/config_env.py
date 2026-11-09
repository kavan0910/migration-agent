"""Environment-variable configuration layer (D-03).

One small entry point for containers and CI, where editing ``config.json`` is awkward::

    from migration_agent.config_env import load_llm_config_from_env, load_settings_from_env
    cfg = load_llm_config_from_env()      # LLMConfig: base file (MA_LLM_CONFIG) + env overrides
    st = load_settings_from_env()         # api_base, max_usd, log_level (not part of LLMConfig)

Precedence (lowest to highest): built-in defaults < ``MA_LLM_CONFIG`` file < ``MA_*`` variables.
An explicit ``--config`` / ``--max-usd`` CLI flag should win over this layer (the caller decides).
Secrets (API keys) are never read here; the LLM library reads them from its own variables.
Documented in docs/deployment.md.
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

from .llm.litellm_client import LLMConfig

MODEL_PREFIX = "MA_LLM_MODEL_"
_LEVELS = {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}


class EnvConfigError(ValueError):
    """An MA_* variable has an unusable value (message names the variable)."""


@dataclass(frozen=True)
class EnvSettings:
    api_base: str | None = None      # MA_LLM_API_BASE: custom endpoint (proxy / Azure / Bedrock gateway)
    max_usd: float | None = None     # MA_MAX_USD: hard budget, same meaning as --max-usd
    log_level: str = "WARNING"       # MA_LOG_LEVEL
    config_path: Path | None = None  # MA_LLM_CONFIG: base config.json


def _get(env: Mapping[str, str], key: str) -> str | None:
    v = env.get(key)
    if v is None or not v.strip():
        return None
    return v.strip()


def _num(env: Mapping[str, str], key: str, cast, minimum=0):
    raw = _get(env, key)
    if raw is None:
        return None
    try:
        val = cast(raw)
    except ValueError:
        raise EnvConfigError(f"{key}={raw!r} is not a valid {cast.__name__}") from None
    if val < minimum:
        raise EnvConfigError(f"{key}={raw!r} must be >= {minimum}")
    return val


def load_settings_from_env(env: Mapping[str, str] | None = None) -> EnvSettings:
    env = os.environ if env is None else env
    level = (_get(env, "MA_LOG_LEVEL") or "WARNING").upper()
    if level not in _LEVELS:
        raise EnvConfigError(f"MA_LOG_LEVEL={level!r} must be one of {sorted(_LEVELS)}")
    path = _get(env, "MA_LLM_CONFIG")
    return EnvSettings(
        api_base=_get(env, "MA_LLM_API_BASE"),
        max_usd=_num(env, "MA_MAX_USD", float),
        log_level=level,
        config_path=Path(path) if path else None,
    )


def load_llm_config_from_env(env: Mapping[str, str] | None = None) -> LLMConfig:
    """Build an LLMConfig from defaults, an optional MA_LLM_CONFIG file and MA_LLM_* overrides.

    Variables: MA_LLM_CONFIG, MA_LLM_MODEL (default model), MA_LLM_MODEL_<TASK> (e.g.
    MA_LLM_MODEL_CHUNK_EXTRACT, ..._CROSS_REF, ..._BUSINESS_RULES, ..._GAP_FILL, ..._VERIFIER),
    MA_LLM_PRICE_DEFAULT ("in,out" USD per 1M tokens), MA_LLM_MAX_RETRIES, MA_LLM_TIMEOUT,
    MA_LLM_MAX_OUTPUT_TOKENS, MA_LLM_BACKOFF_BASE, MA_LLM_BACKOFF_MAX, MA_LLM_API_BASE.
    """
    env = os.environ if env is None else env
    st = load_settings_from_env(env)
    if st.config_path is not None:
        if not st.config_path.is_file():
            raise EnvConfigError(f"MA_LLM_CONFIG={str(st.config_path)!r} does not exist")
        cfg = LLMConfig.load(st.config_path)
    else:
        cfg = LLMConfig()
    upd: dict = {}

    models = dict(cfg.models)
    for key, val in env.items():
        if key.startswith(MODEL_PREFIX) and val.strip():
            task = key[len(MODEL_PREFIX):].lower()
            if task:
                models[task] = val.strip()
    if models != cfg.models:
        upd["models"] = models
    default_model = _get(env, "MA_LLM_MODEL")
    if default_model:
        upd["default_model"] = default_model

    price = _get(env, "MA_LLM_PRICE_DEFAULT")
    if price:
        try:
            a, b = (float(x) for x in price.split(","))
        except ValueError:
            raise EnvConfigError(f"MA_LLM_PRICE_DEFAULT={price!r} must look like '3.0,15.0'") from None
        upd["default_pricing"] = (a, b)

    for var, field, cast in (
        ("MA_LLM_MAX_RETRIES", "max_retries", int),
        ("MA_LLM_TIMEOUT", "timeout", float),
        ("MA_LLM_MAX_OUTPUT_TOKENS", "max_output_tokens", int),
        ("MA_LLM_BACKOFF_BASE", "backoff_base", float),
        ("MA_LLM_BACKOFF_MAX", "backoff_max", float),
    ):
        v = _num(env, var, cast)
        if v is not None:
            upd[field] = v

    # api_base is applied only once LLMConfig grows that field (owned by llm/); until then
    # callers read EnvSettings.api_base. Forward-compatible, never raises.
    if st.api_base and "api_base" in LLMConfig.model_fields:
        upd["api_base"] = st.api_base

    return cfg.model_copy(update=upd) if upd else cfg


def configure_logging(env: Mapping[str, str] | None = None) -> str:
    """Apply MA_LOG_LEVEL to the root logger (stderr). Returns the level name."""
    level = load_settings_from_env(env).log_level
    logging.basicConfig(level=getattr(logging, level), format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    logging.getLogger().setLevel(getattr(logging, level))
    return level
