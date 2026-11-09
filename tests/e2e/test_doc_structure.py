import re
from collections import Counter

import e2e_golden as golden

# Required sections, in order (keyword of the real heading in render.SECTIONS).
REQUIRED_ORDER = ["Purpose", "External Dependencies", "Component Responsibilities", "Endpoints",
                  "Data Model", "Business Rules", "Configuration", "Coverage Matrix"]
ANCHOR = re.compile(r'<a id="([^"]+)"></a>')
LINK = re.compile(r"\]\(#([A-Za-z0-9_-]+)\)")


def headings(doc: str) -> list[str]:
    return [ln for ln in doc.splitlines() if re.match(r"#{1,2} ", ln)]


def test_title_and_heading_skeleton_is_stable(run):
    hs = headings(run.doc)
    assert hs[0] == f"# Understanding: {run.proj.meta().name}"
    golden.check("doc.headings.json", hs[1:])      # all repos share the same fixed skeleton


def test_required_sections_in_order(run):
    hs = [h for h in headings(run.doc) if h.startswith("## ")]
    pos = []
    for key in REQUIRED_ORDER:
        idx = [i for i, h in enumerate(hs) if key in h]
        assert idx, f"missing section {key!r} in {hs}"
        pos.append(idx[0])
    assert pos == sorted(pos), f"sections out of order: {list(zip(REQUIRED_ORDER, pos))}"
    nums = [int(m.group(1)) for h in hs if (m := re.match(r"## (\d+)\.", h))]
    assert nums == list(range(1, len(nums) + 1))


def test_coverage_section_has_inventory_and_unmapped(run):
    cov = run.doc.split("## 15. Coverage Matrix", 1)[1]
    assert "Inventory:" in cov and "source_file:" in cov
    assert re.search(r"^- unmapped: 0$", cov, re.M) and re.search(r"^- mapped: [1-9]\d*$", cov, re.M)


def test_each_node_anchored_exactly_once(run):
    anchors = Counter(ANCHOR.findall(run.doc))
    for n in run.graph().nodes:
        assert anchors[n.id] == 1, f"{n.id} anchored {anchors[n.id]}x"
    dup = {a: c for a, c in anchors.items() if c > 1}
    assert not dup, dup


def test_internal_links_resolve(run):
    anchors = set(ANCHOR.findall(run.doc))
    links = set(LINK.findall(run.doc))
    assert links, "expected cross-reference links"
    assert not (links - anchors), sorted(links - anchors)[:10]


def test_sections_list_the_right_node_types(run):
    g = run.graph()
    body = run.doc
    def section(num):
        m = re.search(rf"^## {num}\. .*?(?=^## \d+\. |\Z)", body, re.M | re.S)
        return m.group(0)
    for t, num in (("endpoint", 4), ("data_entity", 5), ("business_rule", 6), ("config", 9), ("external_dependency", 2)):
        sec = section(num)
        for n in g.nodes:
            if n.type == t:
                assert f'<a id="{n.id}"></a>' in sec, (t, n.id)


def test_doc_is_deterministic_for_same_graph(run):
    from migration_agent.render import render_understanding  # pure function of the graph
    from migration_agent.analyze.coverage import check_coverage
    from migration_agent.pipeline import Inventory
    inv = Inventory.model_validate_json(run.proj.inventory_path.read_text())
    cov = check_coverage(inv, run.graph())
    meta = {"name": run.proj.meta().name, "source": run.proj.meta().source}
    args = dict(coverage=cov.model_dump(exclude={"unmapped_items"}) | {"unmapped_ids": []})
    assert render_understanding(run.graph(), meta, inv.counts, **args) == render_understanding(run.graph(), meta, inv.counts, **args)


def test_graph_html_written(run):
    html = (run.root / "docs" / "graph.html").read_text()
    assert "<html" in html.lower() and run.graph().nodes[0].id in html


def test_facts_only_render_skeleton(run, tmp_path):
    """`render` (no LLM) writes the facts-only skeleton: a different, smaller contract than the analyze doc."""
    import shutil
    from click.testing import CliRunner
    from migration_agent.cli import main
    root = tmp_path / "p"
    shutil.copytree(run.root, root)
    res = CliRunner().invoke(main, ["render", str(root)])
    assert res.exit_code == 0, res.output
    doc = (root / "docs" / "understanding.latest.md").read_text()
    hs = [h for h in headings(doc) if h.startswith("## ")]
    assert hs[0] == "## Coverage inventory" and "## Components" in hs and "## Endpoints" in hs
    assert hs.index("## Components") < hs.index("## Endpoints")
    anchors = Counter(ANCHOR.findall(doc))
    assert anchors and all(c == 1 for c in anchors.values())
