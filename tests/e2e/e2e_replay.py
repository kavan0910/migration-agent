"""Deterministic replay LLM for the end-to-end suite.

``ReplayBackend.transport`` is a drop-in for the litellm transport (``LiteLLMClient(transport=...)``), so the
real client code (cost, cache, budget, audit) runs. Canned answers are derived from
``eval/repos/<repo>/expected.json`` with evidence taken from the real repo files, so they are always valid.

Task detection: each task tier is configured with its own model name ``fake/<task>`` (see ``write_config``).
"""
from __future__ import annotations

import json
import re
from pathlib import Path

from migration_agent.evalharness import EVAL_REPOS, load_expected
from migration_agent.pipeline import ROUTE_RES

TASKS = ("chunk_extract", "cross_ref", "business_rules", "gap_fill", "verifier")
HDR = re.compile(r"^=== (.+?) \(lines (\d+)-(\d+)\) ===$", re.M)
NUMBERED = re.compile(r"^\s*(\d+)\| ?(.*)$")
TYPE_MAP = {"entity": "data_entity", "rule": "business_rule", "component": "component", "endpoint": "endpoint"}
EDGE_MAP = {"calls": "calls", "persists": "writes", "relates_to": "depends_on"}  # handled_by has no graph equivalent
UNVERIFIABLE_QUOTE = "this sentence appears in no document anywhere zzqx"

DOC_ONLY_SENTENCE = "Customers receive a five percent loyalty discount on every purchase."


def write_config(path: Path, price_in: float = 3.0, price_out: float = 15.0) -> Path:
    cfg = {"llm": {
        "models": {t: f"fake/{t}" for t in TASKS},
        "default_model": "fake/chunk_extract",
        "pricing": {t: [price_in, price_out] for t in TASKS},
        "default_pricing": [price_in, price_out],
        "max_output_tokens": 4096, "max_retries": 0, "backoff_base": 0.0, "backoff_max": 0.0,
    }}
    path.write_text(json.dumps(cfg))
    return path


def user_doc_text(repo: str) -> str:
    """Requirements doc with one contradiction (vs. the first rule), one doc-only claim; the rest of the
    system is deliberately undocumented (code-only)."""
    rule = load_expected(repo)["rules"][0]
    return (f"# Requirements\n\n## Business rules\n\n"
            f"The opposite is true in production: it is NOT the case that {rule['text'][0].lower()}{rule['text'][1:]}.\n\n"
            f"## Pricing\n\n{DOC_ONLY_SENTENCE}\n")


def _lines(repo: str, rel: str) -> list[str]:
    return (EVAL_REPOS / repo / rel).read_text(errors="replace").splitlines()


def first_code_line(repo: str, rel: str) -> tuple[int, str]:
    for i, ln in enumerate(_lines(repo, rel), 1):
        if ln.strip():
            return i, ln.strip()
    return 1, ""


def _route_line(repo: str, mod: str, name: str) -> tuple[int, str]:
    method, _, path = name.partition(" ")
    best = None
    for i, ln in enumerate(_lines(repo, mod), 1):
        for rx in ROUTE_RES:
            m = rx.search(ln)
            if not m:
                continue
            g = [x for x in m.groups() if x]
            rel = (g[1] if len(g) > 1 else "").strip("/")
            meth = (g[0] if g else "").upper()
            ok_m = method == "ANY" or meth == method or meth in ("PATH", "ROUTE")
            if ok_m and path.strip("/").endswith(rel) and (best is None or len(rel) > best[0]):
                best = (len(rel), i, ln.strip())
            break
    if best:
        return best[1], best[2]
    return first_code_line(repo, mod)


class ReplayBackend:
    def __init__(self, repo: str, root: Path | None = None, price: tuple[float, float] = (3.0, 15.0)):
        self.repo, self.root, self.price = repo, root, price
        self.expected = load_expected(repo)
        self.calls: list[dict] = []     # task, tokens_in, tokens_out, cost (sum == what the client should audit)

    # -- transport (litellm.completion stand-in)
    def transport(self, **kw):
        model = kw["model"]
        task = model.split("/")[-1]
        msgs = kw["messages"]
        system = msgs[0]["content"] if isinstance(msgs[0]["content"], str) else msgs[0]["content"][0]["text"]
        prompt = msgs[1]["content"]
        text = self.respond(task, system, prompt)
        tin, tout = max(1, (len(system) + len(prompt)) // 4), max(1, len(text) // 4)
        pin, pout = self.price
        self.calls.append({"task": task, "tokens_in": tin, "tokens_out": tout,
                           "cost": (tin * pin + tout * pout) / 1_000_000})
        return {"choices": [{"message": {"content": text}}], "usage": {"prompt_tokens": tin, "completion_tokens": tout}}

    def respond(self, task: str, system: str, prompt: str) -> str:
        if task == "chunk_extract":
            return json.dumps(self._reanalysis(prompt) if "Target item" in prompt else self._chunk(prompt, rules=False))
        if task == "business_rules":
            return json.dumps(self._chunk(prompt, rules=True))
        if task == "verifier":
            return json.dumps(self._verifier(prompt))
        if task == "gap_fill":
            return json.dumps({"action": "unknown"})
        return json.dumps({"resolutions": []})

    # -- analysis
    def _chunk(self, prompt: str, rules: bool) -> dict:
        files = {m.group(1) for m in HDR.finditer(prompt)}
        nodes, edges = [], []
        e = self.expected

        def ev(mod, line, quote, end=None):
            return [{"file": mod, "line_start": line, "line_end": end or line, "quote": quote}]

        if rules:
            for r in e["rules"]:
                if r["module"] in files:
                    s = r["evidence"][0]
                    quote = _lines(self.repo, r["module"])[s["start_line"] - 1].strip()
                    nodes.append({"type": "business_rule", "module": r["module"], "qualified_symbol": r["qualified_symbol"],
                                  "name": r["name"], "responsibility": r["text"],
                                  "evidence": ev(r["module"], s["start_line"], quote, s["end_line"])})
            return {"chunk_id": "x", "nodes": nodes, "edges": [], "unresolved": []}
        for cat in ("components", "endpoints", "entities"):
            for n in e[cat]:
                if n["module"] not in files:
                    continue
                line, quote = (_route_line(self.repo, n["module"], n["name"]) if cat == "endpoints"
                               else first_code_line(self.repo, n["module"]))
                nodes.append({"type": TYPE_MAP[n["type"]], "module": n["module"], "qualified_symbol": n["qualified_symbol"],
                              "name": n["name"], "responsibility": f"{n['name']} ({n['type']}) - replay",
                              "evidence": ev(n["module"], line, quote)})
        for ed in e["edges"]:
            st, smod, ssym = ed["source"].split("|", 2)
            tt, tmod, tsym = ed["target"].split("|", 2)
            if smod in files and ed["type"] in EDGE_MAP:
                line, quote = first_code_line(self.repo, smod)
                edges.append({"source": {"type": TYPE_MAP[st], "module": smod, "qualified_symbol": ssym},
                              "target": {"type": TYPE_MAP[tt], "module": tmod, "qualified_symbol": tsym},
                              "type": EDGE_MAP[ed["type"]], "evidence": ev(smod, line, quote)})
        return {"chunk_id": "x", "nodes": nodes, "edges": edges, "unresolved": []}

    def _reanalysis(self, prompt: str) -> dict:
        m = re.search(r"Target item (\S+): (\w+) '(.*?)' \(module '(.*?)'\)", prompt)
        assert m, "reanalysis prompt format changed"
        _, typ, sym, mod = m.groups()
        for ln in prompt.splitlines():
            nm = NUMBERED.match(ln)
            if nm and nm.group(2).strip():
                line, quote = int(nm.group(1)), nm.group(2).strip()
                break
        else:
            raise AssertionError("no numbered code line in reanalysis prompt")
        return {"chunk_id": "x", "edges": [], "unresolved": [], "nodes": [
            {"type": typ, "module": mod, "qualified_symbol": sym, "name": sym,
             "responsibility": "REVISED by reanalysis (replay)",
             "evidence": [{"file": mod, "line_start": line, "line_end": line, "quote": quote}]}]}

    # -- verifier
    def _verifier(self, prompt: str) -> dict:
        m = re.search(r"Document source: (.+)", prompt)
        if m:   # claim extraction
            src = m.group(1).strip()
            body = prompt.split("<<<DOC_BEGIN>>>", 1)[1].split("<<<DOC_END>>>", 1)[0]
            head = next(ln for ln in body.splitlines() if ln.startswith("#"))
            head = re.sub(r"\s*\{#[\w-]+\}\s*$", "", head)
            return {"claims": [{"text": f"{src} states: {head}", "quote": head.strip(), "anchor": "", "item_ids": []}]}
        return self._compare()

    def _compare(self) -> dict:
        from migration_agent.graph import load_latest
        g = load_latest(self.root / "graph")
        doc = (self.root / "docs" / "understanding.latest.md").read_text()
        rule_text = self.expected["rules"][0]["text"]
        rule = next(n for n in g.nodes if n.type == "business_rule" and n.responsibility == rule_text)
        udoc = "requirements.md"  # UserDoc.path is relative to user_docs/
        # two code-only items in the same directory (grouped) + one elsewhere
        by_dir: dict[str, list] = {}
        for n in g.nodes:
            if n.type == "component" and n.module:
                by_dir.setdefault(n.module.rsplit("/", 1)[0] if "/" in n.module else "(root)", []).append(n)
        group = next(v for v in by_dir.values() if len(v) >= 3)
        a, b, c = group[0], group[1], group[2]
        q = lambda n: {"source": "understanding", "text": f"**{n.id}** {n.name}"}  # noqa: E731
        assert q(a)["text"] in doc and rule_text in doc
        return {"findings": [
            {"kind": "contradiction", "severity": "high", "title": "Rule disputed by requirements",
             "detail": "Docs say the opposite.", "item_ids": [rule.id],
             "quotes": [{"source": "understanding", "text": rule_text},
                        {"source": udoc, "text": "it is NOT the case that"}]},
            {"kind": "doc_only", "severity": "medium", "title": "Loyalty discount not in code", "item_ids": [],
             "quotes": [{"source": udoc, "text": DOC_ONLY_SENTENCE}]},
            {"kind": "code_only", "severity": "medium", "title": f"{a.name} undocumented", "item_ids": [a.id],
             "quotes": [q(a)], "area": "x"},
            {"kind": "code_only", "severity": "medium", "title": f"{b.name} undocumented", "item_ids": [b.id],
             "quotes": [q(b)], "area": "x"},
            {"kind": "code_only", "severity": "medium", "title": f"{c.name} undocumented", "item_ids": [c.id],
             "quotes": [q(c)], "area": "x"},
            {"kind": "contradiction", "severity": "high", "title": "Fabricated quote", "item_ids": [],
             "quotes": [{"source": "understanding", "text": UNVERIFIABLE_QUOTE}]},
            {"kind": "bogus_kind", "severity": "low", "title": "Invalid kind", "quotes": []},
        ]}
