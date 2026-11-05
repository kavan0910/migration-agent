"""LiteLLM-backed LLMClient (B-01): model tiers, retries, budget guard, disk cache, audit."""
from __future__ import annotations

import hashlib
import json
import random
import threading
import time
from pathlib import Path
from typing import Any, Callable

from pydantic import BaseModel, Field

from ..audit import AuditLog
from .base import BudgetExceeded, LLMResponse, parse_json

DEFAULT_TIERS: dict[str, str] = {
    "chunk_extract": "claude-haiku-5-5",
    "cross_ref": "claude-sonnet-5-5",
    "business_rules": "claude-sonnet-5-5",
    "gap_fill": "claude-sonnet-5-5",
    "verifier": "claude-sonnet-5-5",
}

# USD per 1M tokens: (input, output)
DEFAULT_PRICING: dict[str, tuple[float, float]] = {
    "claude-haiku-5-5": (1.0, 5.0),
    "claude-sonnet-5-5": (3.0, 15.0),
}

REPAIR_INSTRUCTION = (
    "Your previous reply was not valid JSON. Reply again with ONLY the valid JSON value, "
    "no prose and no code fences."
)


class LLMConfig(BaseModel):
    models: dict[str, str] = Field(default_factory=lambda: dict(DEFAULT_TIERS))
    default_model: str = "claude-sonnet-5-5"
    pricing: dict[str, tuple[float, float]] = Field(default_factory=lambda: dict(DEFAULT_PRICING))
    default_pricing: tuple[float, float] = (3.0, 15.0)
    max_retries: int = 4
    backoff_base: float = 1.0
    backoff_max: float = 30.0
    timeout: float = 120.0
    max_output_tokens: int = 4096

    def model_for(self, task: str) -> str:
        return self.models.get(task, self.default_model)

    def price_for(self, model: str) -> tuple[float, float]:
        if model in self.pricing:
            return self.pricing[model]
        bare = model.split("/")[-1]
        return self.pricing.get(bare, self.default_pricing)

    @classmethod
    def load(cls, path: str | Path) -> "LLMConfig":
        """Load from config.json; reads the top-level 'llm' key if present, else the whole file."""
        data = json.loads(Path(path).read_text())
        if isinstance(data, dict) and isinstance(data.get("llm"), dict):
            data = data["llm"]
        return cls.model_validate(data)


class Budget:
    def __init__(self, max_usd: float | None = None, max_tokens: int | None = None):
        self.max_usd = max_usd
        self.max_tokens = max_tokens
        self.spent_usd = 0.0
        self.used_tokens = 0
        self._lock = threading.Lock()

    def check(self, est_usd: float, est_tokens: int) -> None:
        with self._lock:
            if self.max_usd is not None and self.spent_usd + est_usd > self.max_usd:
                raise BudgetExceeded(
                    f"USD budget: spent {self.spent_usd:.4f} + est {est_usd:.4f} > {self.max_usd:.4f}")
            if self.max_tokens is not None and self.used_tokens + est_tokens > self.max_tokens:
                raise BudgetExceeded(
                    f"token budget: used {self.used_tokens} + est {est_tokens} > {self.max_tokens}")

    def add(self, usd: float, tokens: int) -> None:
        with self._lock:
            self.spent_usd += usd
            self.used_tokens += tokens


def count_tokens(text: str) -> int:
    """Cheap estimate (~4 chars/token) used for pre-call budget checks."""
    return max(1, len(text) // 4)


def _cost(config: LLMConfig, model: str, tin: int, tout: int) -> float:
    pin, pout = config.price_for(model)
    return (tin * pin + tout * pout) / 1_000_000


def estimate_cost(tasks: list[tuple[str, int, int]], config: LLMConfig | None = None) -> float:
    config = config or LLMConfig()
    return sum(_cost(config, config.model_for(t), tin, tout) for t, tin, tout in tasks)


def _is_retryable(exc: BaseException) -> bool:
    if isinstance(exc, (TimeoutError, ConnectionError)):
        return True
    status = getattr(exc, "status_code", None)
    if isinstance(status, int):
        return status == 429 or status >= 500
    name = type(exc).__name__.lower()
    return any(k in name for k in ("timeout", "ratelimit", "serviceunavailable",
                                   "internalserver", "apiconnection", "badgateway"))


def _default_transport() -> Callable[..., Any]:
    import litellm  # lazy: optional dependency

    return litellm.completion


def _get(obj: Any, key: str, default: Any = None) -> Any:
    if isinstance(obj, dict):
        return obj.get(key, default)
    return getattr(obj, key, default)


class LiteLLMClient:
    def __init__(
        self,
        config: LLMConfig | None = None,
        *,
        budget: Budget | None = None,
        cache_dir: str | Path | None = None,
        audit: AuditLog | None = None,
        agent: str = "llm",
        transport: Callable[..., Any] | None = None,
        sleep: Callable[[float], None] | None = None,
    ):
        self.config = config or LLMConfig()
        self.budget = budget
        self.cache_dir = Path(cache_dir) if cache_dir else None
        self.audit = audit
        self.agent = agent
        self._transport = transport
        self._sleep = sleep  # None -> time.sleep resolved at call time (patchable)

    # -- helpers
    def _call_transport(self, **kw):
        if self._transport is None:
            self._transport = _default_transport()
        return self._transport(**kw)

    def _do_sleep(self, s: float) -> None:
        (self._sleep or time.sleep)(s)

    def _build_messages(self, model: str, system: str, prompt: str) -> list[dict]:
        if "claude" in model or "anthropic" in model:
            sys_msg: dict = {"role": "system", "content": [
                {"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}]}
        else:
            sys_msg = {"role": "system", "content": system}
        return [sys_msg, {"role": "user", "content": prompt}]

    def _cache_path(self, model: str, system: str, prompt: str, version: str) -> Path | None:
        if not self.cache_dir:
            return None
        key = hashlib.sha256("\x00".join([model, system, prompt, version]).encode()).hexdigest()
        return self.cache_dir / f"{key}.json"

    def _with_retries(self, kw: dict) -> Any:
        cfg = self.config
        attempt = 0
        while True:
            try:
                return self._call_transport(**kw)
            except Exception as e:  # noqa: BLE001
                if not _is_retryable(e) or attempt >= cfg.max_retries:
                    raise
                delay = min(cfg.backoff_max, cfg.backoff_base * (2 ** attempt))
                self._do_sleep(delay + random.uniform(0, delay * 0.25))
                attempt += 1

    @staticmethod
    def _usage(resp: Any, prompt_text: str, out_text: str) -> tuple[int, int]:
        usage = _get(resp, "usage")
        tin = _get(usage, "prompt_tokens") if usage is not None else None
        tout = _get(usage, "completion_tokens") if usage is not None else None
        return (int(tin) if tin is not None else count_tokens(prompt_text),
                int(tout) if tout is not None else count_tokens(out_text))

    @staticmethod
    def _text(resp: Any) -> str:
        choice = _get(resp, "choices")[0]
        return _get(_get(choice, "message"), "content") or ""

    # -- public
    def complete(self, *, task: str, system: str, prompt: str, json_mode: bool = True,
                 prompt_version: str = "v1") -> LLMResponse:
        cfg = self.config
        model = cfg.model_for(task)
        input_hash = hashlib.sha256("\x00".join([model, system, prompt, prompt_version]).encode()).hexdigest()
        cpath = self._cache_path(model, system, prompt, prompt_version)

        if cpath is not None and cpath.exists():
            try:
                hit = LLMResponse.model_validate_json(cpath.read_text())
                hit = hit.model_copy(update={"cached": True, "cost_usd": 0.0})
                self._audit(task, hit, prompt_version, input_hash, cached=True)
                return hit
            except Exception:  # corrupt cache entry -> refetch
                pass

        est_in = count_tokens(system) + count_tokens(prompt)
        est_out = cfg.max_output_tokens
        if self.budget:
            self.budget.check(_cost(cfg, model, est_in, est_out), est_in + est_out)

        kw: dict[str, Any] = {
            "model": model,
            "messages": self._build_messages(model, system, prompt),
            "max_tokens": cfg.max_output_tokens,
            "timeout": cfg.timeout,
        }
        if json_mode:
            kw["response_format"] = {"type": "json_object"}

        resp = self._with_retries(kw)
        text = self._text(resp)
        tin, tout = self._usage(resp, system + prompt, text)

        if json_mode:
            try:
                parse_json(text)
            except (json.JSONDecodeError, ValueError):
                if self.budget:
                    self.budget.add(_cost(cfg, model, tin, tout), tin + tout)
                kw2 = dict(kw)
                kw2["messages"] = kw["messages"] + [
                    {"role": "assistant", "content": text},
                    {"role": "user", "content": REPAIR_INSTRUCTION}]
                resp2 = self._with_retries(kw2)
                text2 = self._text(resp2)
                tin2, tout2 = self._usage(resp2, system + prompt + text, text2)
                text, tin, tout = text2, tin + tin2, tout + tout2
                # budget for first attempt already added; add only second
                cost = _cost(cfg, model, tin, tout)
                out = LLMResponse(text=text, model=model, tokens_in=tin, tokens_out=tout, cost_usd=cost)
                if self.budget:
                    self.budget.add(_cost(cfg, model, tin2, tout2), tin2 + tout2)
                return self._finish(task, out, prompt_version, input_hash, cpath, parse_ok=_json_ok(text))

        cost = _cost(cfg, model, tin, tout)
        out = LLMResponse(text=text, model=model, tokens_in=tin, tokens_out=tout, cost_usd=cost)
        if self.budget:
            self.budget.add(cost, tin + tout)
        return self._finish(task, out, prompt_version, input_hash, cpath, parse_ok=True)

    def _finish(self, task, out: LLMResponse, version, input_hash, cpath, parse_ok: bool) -> LLMResponse:
        if cpath is not None and parse_ok:
            cpath.parent.mkdir(parents=True, exist_ok=True)
            tmp = cpath.with_suffix(".tmp")
            tmp.write_text(out.model_dump_json())
            tmp.replace(cpath)
        self._audit(task, out, version, input_hash, cached=False)
        return out

    def _audit(self, task, out: LLMResponse, version, input_hash, cached: bool) -> None:
        if self.audit:
            self.audit.record(self.agent, "llm_call", model=out.model, tokens_in=out.tokens_in,
                              tokens_out=out.tokens_out, cost_usd=out.cost_usd,
                              prompt_version=version, input_hash=input_hash,
                              task=task, cached=cached)


def _json_ok(text: str) -> bool:
    try:
        parse_json(text)
        return True
    except (json.JSONDecodeError, ValueError):
        return False
