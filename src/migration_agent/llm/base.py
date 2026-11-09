"""LLM contract shared by every Phase B component (B-01 implements adapters behind it)."""
from __future__ import annotations

import json
from typing import Callable, Protocol

from pydantic import BaseModel


class LLMResponse(BaseModel):
    text: str
    model: str
    tokens_in: int = 0
    tokens_out: int = 0
    cost_usd: float = 0.0
    cached: bool = False


class LLMClient(Protocol):
    def complete(self, *, task: str, system: str, prompt: str, json_mode: bool = True,
                 prompt_version: str = "v1") -> LLMResponse:
        """`task` selects the model tier (e.g. 'chunk_extract', 'cross_ref', 'business_rules',
        'gap_fill', 'verifier'). Raises BudgetExceeded when the hard budget is hit."""


class BudgetExceeded(RuntimeError):
    pass


class LLMError(RuntimeError):
    """Base for provider-level failures raised by LLM clients (D-05)."""


class QuotaExhausted(BudgetExceeded):
    """Provider quota is exhausted for a long time (e.g. daily quota, retryDelay of hours): fail fast
    instead of sleeping. Subclasses BudgetExceeded so runs stop gracefully with partial results."""


class LLMAuthError(LLMError):
    """401/403: credentials or permissions are wrong. Retrying (or continuing the run) cannot help."""


class FakeLLM:
    """Deterministic test double. `responder(task, system, prompt) -> str` returns the raw text
    (usually JSON). Every call is recorded in `.calls` so tests can assert on prompts."""

    def __init__(self, responder: Callable[[str, str, str], str]):
        self.responder = responder
        self.calls: list[dict] = []

    def complete(self, *, task, system, prompt, json_mode=True, prompt_version="v1") -> LLMResponse:
        self.calls.append({"task": task, "system": system, "prompt": prompt, "prompt_version": prompt_version})
        text = self.responder(task, system, prompt)
        return LLMResponse(text=text, model="fake", tokens_in=len(prompt) // 4, tokens_out=len(text) // 4)


def parse_json(text: str):
    """Tolerant JSON extraction (strips code fences / leading prose)."""
    t = text.strip()
    if t.startswith("```"):
        t = t.split("\n", 1)[1].rsplit("```", 1)[0]
    try:
        return json.loads(t)
    except json.JSONDecodeError:
        a, b = t.find("{"), t.rfind("}")
        if a >= 0 and b > a:
            return json.loads(t[a:b + 1])
        raise
