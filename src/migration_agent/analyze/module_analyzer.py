"""B-03 module analyzer: LLM chunk extraction -> validated ChunkPatch."""
from __future__ import annotations

import json
import typing
from pathlib import Path

from pydantic import ValidationError

from ..graph.models import EdgeType, NodeType
from ..llm.base import LLMClient, parse_json
from .patch import ChunkPatch

PROMPT_VERSION = "chunk_extract.v1"
PROMPTS_DIR = Path(__file__).parent / "prompts"
NODE_TYPES = frozenset(typing.get_args(NodeType))
EDGE_TYPES = frozenset(typing.get_args(EdgeType))


class AnalysisError(RuntimeError):
    pass


def load_prompt(name: str) -> str:
    return (PROMPTS_DIR / name).read_text(encoding="utf-8")


def render(template: str, **values: str) -> str:
    for k, v in values.items():
        template = template.replace("{{" + k + "}}", v)
    return template


def _ref_ok(ref, node_types=NODE_TYPES) -> bool:
    return isinstance(ref, dict) and ref.get("type") in node_types


def sanitize_patch(data, chunk_id: str, node_types=NODE_TYPES) -> dict:
    """Drop nodes/edges/unresolved with invalid types; force chunk_id."""
    if not isinstance(data, dict):
        raise ValueError("top-level JSON must be an object")
    out = dict(data)
    out["chunk_id"] = chunk_id
    out["nodes"] = [n for n in (data.get("nodes") or []) if isinstance(n, dict) and n.get("type") in node_types]
    out["edges"] = [e for e in (data.get("edges") or [])
                    if isinstance(e, dict) and e.get("type") in EDGE_TYPES
                    and _ref_ok(e.get("source")) and _ref_ok(e.get("target"))]
    out["unresolved"] = [u for u in (data.get("unresolved") or [])
                         if isinstance(u, dict) and _ref_ok(u.get("from_node"))]
    return out


def run_patch_task(llm: LLMClient, *, task: str, system: str, prompt: str, chunk_id: str,
                   prompt_version: str, node_types=NODE_TYPES) -> ChunkPatch:
    """Call the LLM, parse + validate; retry once with the error appended, then raise."""
    current = prompt
    last_err = ""
    for attempt in range(2):
        resp = llm.complete(task=task, system=system, prompt=current, json_mode=True,
                            prompt_version=prompt_version)
        try:
            return ChunkPatch.model_validate(sanitize_patch(parse_json(resp.text), chunk_id, node_types))
        except (ValueError, ValidationError, json.JSONDecodeError) as e:  # ValidationError is a ValueError
            last_err = str(e)
            current = (prompt + "\n\nYour previous output was invalid:\n" + last_err[:2000]
                       + "\nReturn corrected strict JSON only, matching the schema.")
    raise AnalysisError(f"{task} for chunk {chunk_id!r} produced invalid output twice: {last_err[:500]}")


def analyze_chunk(llm: LLMClient, chunk_id: str, chunk_text: str, facts_summary: str,
                  graph_summary: str) -> ChunkPatch:
    system = load_prompt("chunk_extract.system.md")
    prompt = render(load_prompt("chunk_extract.user.md"), CHUNK_ID=chunk_id, CHUNK_TEXT=chunk_text,
                    FACTS_SUMMARY=facts_summary, GRAPH_SUMMARY=graph_summary)
    return run_patch_task(llm, task="chunk_extract", system=system, prompt=prompt,
                          chunk_id=chunk_id, prompt_version=PROMPT_VERSION)
