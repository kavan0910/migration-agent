"""C-01/C-02/C-09 blind Verifier: compares docs/understanding.latest.md with user docs.

The LLM only ever sees the understanding doc and converted user docs (never code or traces)."""
from __future__ import annotations

import json
import logging
import os
import re
import tempfile
from pathlib import Path

from ..analyze.module_analyzer import load_prompt as _lp, neutralize as _neutralize_text, render
from ..ingest.secrets import redact_string
from ..llm.base import LLMClient, parse_json
from .models import Finding, FindingsFile, Quote
from .userdocs import UserDoc, load_user_docs

log = logging.getLogger(__name__)
PROMPT_VERSION = "verifier.v1"
PROMPTS_DIR = Path(__file__).parent / "prompts"
SEV_RANK = {"high": 0, "medium": 1, "low": 2}
KINDS = {"contradiction", "doc_only", "code_only", "doc_vs_doc"}
ID_RE = re.compile(r"\b[A-Z]{2,5}-\d{4}\b")
UNDERSTANDING = "understanding"


def load_prompt(name: str) -> str:
    return (PROMPTS_DIR / name).read_text(encoding="utf-8")


def _norm(s: str) -> str:
    return " ".join(str(s).split())


def _neutralize(text: str) -> str:
    """Stop doc text from forging our delimiters."""
    return _neutralize_text(re.sub(r"<<<([A-Za-z_]+)>>>", r"<<\1>>", text))


def verify_quote(quote: Quote, sources: dict[str, str]) -> bool:
    text = sources.get(quote.source)
    q = _norm(quote.text)
    return bool(q) and text is not None and q in _norm(text)


def _claims(llm, source: str, text: str) -> list[dict]:
    system = load_prompt("claims.system.md")
    prompt = render(load_prompt("claims.user.md"), source=source, document=_neutralize(text))
    try:
        data = parse_json(llm.complete(task="verifier", system=system, prompt=prompt, json_mode=True,
                                       prompt_version=PROMPT_VERSION).text)
    except (ValueError, json.JSONDecodeError) as e:
        log.warning("claim extraction failed for %s: %s", source, e)
        return []
    out = []
    for c in (data.get("claims") if isinstance(data, dict) else None) or []:
        if isinstance(c, dict) and c.get("text") and c.get("quote"):
            out.append({"source": source, "text": str(c["text"]), "quote": str(c["quote"]),
                        "anchor": str(c.get("anchor") or ""),
                        "item_ids": [str(i) for i in (c.get("item_ids") or []) if isinstance(i, str)]})
    return out


def _graph(project):
    try:
        from ..graph.store import load_latest
        return load_latest(project.graph_dir)
    except Exception as e:
        log.info("no graph available for area derivation: %s", e)
        return None


def _area_of(item_ids: list[str], graph, fallback: str) -> str:
    if graph is not None:
        nodes = {n.id: n for n in graph.nodes}
        for i in item_ids:
            n = nodes.get(i)
            if n is not None and n.module:
                d = n.module.replace("\\", "/").rsplit("/", 1)[0] if "/" in n.module else ""
                return d or "(root)"
    return fallback.strip() or "general"


def _atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".tmp-")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(text)
    os.replace(tmp, path)


def run_verifier(project, llm: LLMClient) -> FindingsFile:
    out_path = Path(project.root) / "feedback" / "findings.json"
    graph = _graph(project)
    gver = graph.version if graph is not None else 0
    # user docs may paste credentials: redact before they reach a prompt, a quote or findings.json
    docs: list[UserDoc] = [d.model_copy(update={"markdown": redact_string(d.markdown)})
                           for d in load_user_docs(project)]
    understanding_path = Path(project.root) / "docs" / "understanding.latest.md"
    if not docs:
        log.info("verifier: no user documents in user_docs/ - nothing to compare against; "
                 "returning empty findings")
        res = FindingsFile(graph_version=gver)
        _atomic_write(out_path, res.model_dump_json(indent=2))
        return res
    if not understanding_path.exists():
        raise FileNotFoundError(f"{understanding_path} missing; run the analysis/render first")
    understanding = redact_string(understanding_path.read_text(encoding="utf-8"))
    sources = {UNDERSTANDING: understanding, **{d.path: d.markdown for d in docs}}

    claims = _claims(llm, UNDERSTANDING, understanding)
    for d in docs:
        claims += _claims(llm, d.path, d.markdown)

    system = load_prompt("compare.system.md")
    prompt = render(load_prompt("compare.user.md"), claims=_neutralize(json.dumps(claims, indent=1)))
    raw: list = []
    try:
        data = parse_json(llm.complete(task="verifier", system=system, prompt=prompt, json_mode=True,
                                       prompt_version=PROMPT_VERSION).text)
        raw = (data.get("findings") if isinstance(data, dict) else None) or []
    except (ValueError, json.JSONDecodeError) as e:
        log.warning("verifier compare step returned invalid JSON: %s", e)

    dropped = 0
    kept: list[dict] = []
    for f in raw:
        if not isinstance(f, dict) or f.get("kind") not in KINDS or not f.get("title"):
            dropped += 1
            continue
        quotes = []
        for q in f.get("quotes") or []:
            if isinstance(q, dict) and q.get("source") and q.get("text"):
                quotes.append(Quote(source=str(q["source"]), text=str(q["text"]), anchor=str(q.get("anchor") or "")))
        for q in quotes:
            q.verified = verify_quote(q, sources)
        if not quotes or not all(q.verified for q in quotes):
            dropped += 1
            continue
        sev = f.get("severity") if f.get("severity") in SEV_RANK else "low"
        ids = [i for i in dict.fromkeys(str(x) for x in (f.get("item_ids") or [])) if ID_RE.fullmatch(i)]
        kept.append({"kind": f["kind"], "severity": sev, "title": str(f["title"]), "detail": str(f.get("detail") or ""),
                     "item_ids": ids, "quotes": quotes, "area": str(f.get("area") or "")})

    final: list[dict] = []
    groups: dict[str, list[dict]] = {}
    for k in kept:
        if k["kind"] == "code_only":
            k["area"] = _area_of(k["item_ids"], graph, k["area"])
            groups.setdefault(k["area"], []).append(k)
        else:
            k["area"] = ""
            final.append(k)
    for area in sorted(groups):
        g = groups[area]
        if len(g) == 1:
            final.append(g[0])
            continue
        quotes, seen = [], set()
        for x in g:
            for q in x["quotes"]:
                if (q.source, _norm(q.text)) not in seen:
                    seen.add((q.source, _norm(q.text)))
                    quotes.append(q)
        final.append({"kind": "code_only", "severity": "low",
                      "title": f"Not covered by user docs: {area} ({len(g)} items)",
                      "detail": "\n".join(f"- {x['title']}" + (f": {x['detail']}" if x["detail"] else "") for x in
                                          sorted(g, key=lambda x: x["title"])),
                      "item_ids": sorted({i for x in g for i in x["item_ids"]}),
                      "quotes": quotes, "area": area})

    final.sort(key=lambda x: (SEV_RANK[x["severity"]], x["kind"], x["area"], x["title"],
                              _norm(x["quotes"][0].text) if x["quotes"] else ""))
    findings = [Finding(id=f"F-{i:04d}", **x) for i, x in enumerate(final, 1)]
    res = FindingsFile(graph_version=gver, findings=findings, dropped_unverifiable=dropped,
                       claims_extracted=len(claims))
    _atomic_write(out_path, res.model_dump_json(indent=2))
    return res
