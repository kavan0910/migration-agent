"""B-08 business rule extractor."""
from __future__ import annotations

from .module_analyzer import load_prompt, render, run_patch_task
from .patch import ChunkPatch

RULES_PROMPT_VERSION = "business_rules.v1"


def extract_rules(llm, chunk_id: str, chunk_text: str, graph_summary: str) -> ChunkPatch:
    system = load_prompt("business_rules.system.md")
    prompt = render(load_prompt("business_rules.user.md"), CHUNK_ID=chunk_id, CHUNK_TEXT=chunk_text,
                    GRAPH_SUMMARY=graph_summary)
    patch = run_patch_task(llm, task="business_rules", system=system, prompt=prompt, chunk_id=chunk_id,
                           prompt_version=RULES_PROMPT_VERSION, node_types=frozenset({"business_rule"}))
    # the extractor only emits rules; edges to the (non-emitted) component nodes are kept
    return patch
