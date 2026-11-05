import json

from migration_agent.analyze.coverage import check_coverage
from migration_agent.analyze.gapfill import fill_gaps
from migration_agent.graph.models import Graph
from migration_agent.llm.base import FakeLLM
from migration_agent.pipeline import Inventory, InventoryItem
from migration_agent.sandbox.tools import ReadOnlyTools


def setup(tmp_path):
    (tmp_path / "a.py").write_text("\n".join(f"line{i}" for i in range(1, 101)))
    items = [InventoryItem(id="I1", kind="source_file", name="a.py", file="a.py"),
             InventoryItem(id="I2", kind="route", name="GET /x", file="a.py", line=50),
             InventoryItem(id="I3", kind="config_key", name="K", file="a.py", line=90)]
    rep = check_coverage(Inventory(items=items, counts={}), Graph())
    return rep, ReadOnlyTools(tmp_path)


def test_map_classify_unknown(tmp_path):
    rep, tools = setup(tmp_path)

    def resp(task, system, prompt):
        if "id=I1" in prompt:
            return json.dumps({"action": "map", "node": {"type": "component", "qualified_symbol": "a", "name": "a"}})
        if "id=I2" in prompt:
            return json.dumps({"action": "classify", "status": "dead_code", "reason": "never registered"})
        return json.dumps({"action": "unknown"})

    llm = FakeLLM(resp)
    r = fill_gaps(llm, rep, Graph(), tools, max_rounds=1)
    assert r.patch.nodes[0].inventory_refs == ["I1"] and r.patch.nodes[0].evidence[0].file == "a.py"
    assert r.classified == {"I2": {"status": "dead_code", "reason": "never registered"}}
    assert all(c["task"] == "gap_fill" for c in llm.calls) and len(llm.calls) == 3
    p2 = [c["prompt"] for c in llm.calls if "id=I2" in c["prompt"]][0]
    assert "line30" in p2 and "line70" in p2 and "line29" not in p2 and "line71" not in p2
    p1 = [c["prompt"] for c in llm.calls if "id=I1" in c["prompt"]][0]
    assert "line1\n" in p1 and "line100" not in p1


def test_max_rounds_respected(tmp_path):
    rep, tools = setup(tmp_path)
    llm = FakeLLM(lambda *a: "garbage")
    r = fill_gaps(llm, rep, Graph(), tools, max_rounds=2)
    assert len(llm.calls) == 6 and not r.classified and not r.patch.nodes
    llm0 = FakeLLM(lambda *a: "")
    fill_gaps(llm0, rep, Graph(), tools, max_rounds=0)
    assert llm0.calls == []


def test_second_round_resolves(tmp_path):
    rep, tools = setup(tmp_path)
    n = {"c": 0}

    def resp(t, s, p):
        n["c"] += 1
        return json.dumps({"action": "unknown"}) if "Round 1" in p else json.dumps(
            {"action": "classify", "status": "excluded", "reason": "fixture"})

    r = fill_gaps(FakeLLM(resp), rep, Graph(), tools, max_rounds=2)
    assert len(r.classified) == 3 and n["c"] == 6
