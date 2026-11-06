"""Tiny safe markdown renderer for docs/understanding.latest.md.

Everything is HTML-escaped first; raw HTML never passes through. The only
markup emitted is a fixed whitelist (h3-h5, p, ul/li, pre/code, strong, em,
code, span). `<a id="COMP-0001"></a>` anchors are recognised by exact pattern.
"""
from __future__ import annotations

import html
import re

ID_RE = re.compile(r"\b([A-Z]{2,6}-\d{4})\b")
_ANCHOR_RE = re.compile(r'<a id="([A-Z]{2,6}-\d{4})"></a>')
_BOLD = re.compile(r"\*\*(.+?)\*\*")
_ITAL = re.compile(r"(?<![\w*])_([^_]+?)_(?![\w])")


def _inline(text: str) -> str:
    text = _ANCHOR_RE.sub(lambda m: f"\x00a{m.group(1).lower()}\x00", text)
    out = []
    for i, seg in enumerate(text.split("`")):
        if i % 2 == 1:
            out.append(f"<code>{html.escape(seg)}</code>")
            continue
        s = html.escape(seg, quote=True)
        s = _BOLD.sub(r"<strong>\1</strong>", s)
        s = _ITAL.sub(r"<em>\1</em>", s)
        s = ID_RE.sub(lambda m: f'<span class="iid" data-id="{m.group(1)}" tabindex="0" role="button">{m.group(1)}</span>', s)
        s = re.sub(r"\x00a([a-z]{2,6}-\d{4})\x00",
                   lambda m: f'<span class="anchor" id="item-{m.group(1).upper()}" data-anchor="{m.group(1).upper()}"></span>', s)
        out.append(s)
    return "".join(out)


def anchors(md: str) -> set[str]:
    return set(_ANCHOR_RE.findall(md))


def _blocks(lines: list[str]) -> str:
    out: list[str] = []
    para: list[str] = []
    in_list = False
    in_code = False
    code: list[str] = []

    def flush_para():
        if para:
            out.append("<p>" + _inline(" ".join(para)) + "</p>")
            para.clear()

    def close_list():
        nonlocal in_list
        if in_list:
            out.append("</ul>")
            in_list = False

    for line in lines:
        if line.strip().startswith("```"):
            if in_code:
                out.append("<pre><code>" + html.escape("\n".join(code)) + "</code></pre>")
                code.clear()
                in_code = False
            else:
                flush_para(); close_list()
                in_code = True
            continue
        if in_code:
            code.append(line)
            continue
        m = re.match(r"^(#{3,6})\s+(.*)$", line)
        if m:
            flush_para(); close_list()
            lvl = min(len(m.group(1)) + 1, 6)
            out.append(f"<h{lvl}>{_inline(m.group(2))}</h{lvl}>")
            continue
        m = re.match(r"^\s*(?:[-*]|\d+\.)\s+(.*)$", line)
        if m:
            flush_para()
            if not in_list:
                out.append("<ul>")
                in_list = True
            out.append("<li>" + _inline(m.group(1)) + "</li>")
            continue
        if not line.strip():
            flush_para(); close_list()
            continue
        if in_list and line.startswith("  "):
            out[-1] = out[-1][:-5] + " " + _inline(line.strip()) + "</li>"
            continue
        close_list()
        para.append(line.strip())
    if in_code:
        out.append("<pre><code>" + html.escape("\n".join(code)) + "</code></pre>")
    flush_para(); close_list()
    return "\n".join(out)


def render_sections(md: str) -> list[dict]:
    """Split on top-level `# `/`## ` headings -> [{id, title, html}]."""
    sections: list[dict] = []
    cur_title, cur_lines = "Overview", []
    in_code = False

    def push():
        body = _blocks(cur_lines)
        if body.strip() or sections:
            sections.append({"id": f"sec-{len(sections)}", "title": cur_title, "html": body})

    for line in md.splitlines():
        if line.strip().startswith("```"):
            in_code = not in_code
        m = None if in_code else re.match(r"^(#{1,2})\s+(.*)$", line)
        if m:
            if cur_lines or sections or cur_title != "Overview":
                push()
            cur_title, cur_lines = m.group(2).strip(), []
        else:
            cur_lines.append(line)
    push()
    return [s for s in sections if s["html"].strip() or s["title"]]
