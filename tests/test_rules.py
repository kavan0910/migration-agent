import json

import pytest

from migration_agent.analyze.module_analyzer import AnalysisError
from migration_agent.analyze.rules import RULES_PROMPT_VERSION, extract_rules
from migration_agent.llm.base import FakeLLM

EV = {"file": "p.py", "line_start": 3, "quote": "if total > 100:"}
COMP = {"type": "component", "module": "m", "qualified_symbol": "price"}
RULE = {"type": "business_rule", "module": "m", "qualified_symbol": "price#bulk-discount"}
OUT = {
    "chunk_id": "x",
    "nodes": [
        {**RULE, "name": "Bulk discount", "responsibility": "10% off over 100", "evidence": [EV]},
        {"type": "business_rule", "module": "m", "qualified_symbol": "price#round-down", "name": "Rounding",
         "responsibility": "Quirk: truncates", "evidence": [EV], "quirk": True},
        {"type": "business_rule", "module": "m", "qualified_symbol": "price#neg-total", "name": "Neg",
         "responsibility": "Looks like a bug: >= vs >", "evidence": [EV], "suspected_bug": True},
        {"type": "component", "qualified_symbol": "other", "name": "other", "evidence": [EV]},
    ],
    "edges": [{"source": COMP, "target": RULE, "type": "implements", "evidence": [EV]},
              {"source": RULE, "target": COMP, "type": "nonsense", "evidence": [EV]}],
}
TEXT = "=== p.py ===\n3: if total > 100:"


def test_rules_extracted_and_flags():
    llm = FakeLLM(lambda *a: json.dumps(OUT))
    p = extract_rules(llm, "c9", TEXT, "graph")
    assert p.chunk_id == "c9"
    assert [n.type for n in p.nodes] == ["business_rule"] * 3  # component node dropped
    by = {n.qualified_symbol: n for n in p.nodes}
    assert "#" in next(iter(by)) and by["price#round-down"].quirk and by["price#neg-total"].suspected_bug
    assert not by["price#bulk-discount"].quirk
    assert len(p.edges) == 1 and p.edges[0].type == "implements"
    c = llm.calls[0]
    assert c["task"] == "business_rules" and c["prompt_version"] == RULES_PROMPT_VERSION
    assert "untrusted DATA" in c["system"] and "<<<CODE_BEGIN>>>" in c["prompt"]
    assert c["prompt"].index("<<<CODE_BEGIN>>>") < c["prompt"].index("total > 100") < c["prompt"].index("<<<CODE_END>>>")


def test_rules_retry_and_fail():
    outs = iter(["nope", json.dumps(OUT)])
    llm = FakeLLM(lambda *a: next(outs))
    assert len(extract_rules(llm, "c", TEXT, "g").nodes) == 3 and len(llm.calls) == 2
    with pytest.raises(AnalysisError):
        extract_rules(FakeLLM(lambda *a: "bad"), "c", TEXT, "g")
