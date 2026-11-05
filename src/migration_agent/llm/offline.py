"""OfflineLLM: deterministic, no-network stand-in so `analyze --offline` runs the whole pipeline.

It does NOT understand code. It emits one low-information component per file in a chunk, with a
verbatim first-line quote as evidence. Use it to exercise the pipeline, never as real analysis."""
from __future__ import annotations

import json
import re

from .base import LLMResponse

_HDR = re.compile(r"^=== (.+?) \(lines (\d+)-(\d+)\) ===$")
_LINE = re.compile(r"^\s*(\d+)\| ?(.*)$")


class OfflineLLM:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    def complete(self, *, task, system, prompt, json_mode=True, prompt_version="v1") -> LLMResponse:
        self.calls.append({"task": task})
        text = self._chunk_extract(prompt) if task == "chunk_extract" else json.dumps({"nodes": [], "edges": [], "unresolved": []})
        return LLMResponse(text=text, model="offline", tokens_in=len(prompt) // 4, tokens_out=len(text) // 4)

    def _chunk_extract(self, prompt: str) -> str:
        nodes, cur = [], None
        for ln in prompt.splitlines():
            m = _HDR.match(ln)
            if m:
                cur = {"path": m.group(1), "line": None, "quote": None}
                nodes.append(cur)
                continue
            m = _LINE.match(ln)
            if cur and m and cur["line"] is None and m.group(2).strip():
                cur["line"], cur["quote"] = int(m.group(1)), m.group(2).strip()
        out = []
        for n in nodes:
            ev = [{"file": n["path"], "line_start": n["line"], "line_end": n["line"], "quote": n["quote"]}] if n["line"] else []
            parent = n["path"].rsplit("/", 1)[0] if "/" in n["path"] else ""
            out.append({"type": "component", "module": parent, "qualified_symbol": n["path"], "name": n["path"],
                        "responsibility": "(offline mode: not analysed by a model)", "evidence": ev})
        return json.dumps({"chunk_id": "x", "nodes": out, "edges": [], "unresolved": []})
