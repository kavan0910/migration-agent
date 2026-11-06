"""C-01 user document conversion: user_docs/* -> markdown with section anchors."""
from __future__ import annotations

import html as _html
import logging
import re
import zipfile
from html.parser import HTMLParser
from pathlib import Path
from xml.etree import ElementTree as ET

from pydantic import BaseModel, Field

log = logging.getLogger(__name__)
SUPPORTED = {".md", ".txt", ".html", ".htm", ".docx", ".pdf"}
_HEAD = re.compile(r"^(#{1,6})\s+(.*?)\s*(\{#[\w-]+\})?\s*$")


class Section(BaseModel):
    anchor: str
    title: str
    level: int = 1


class UserDoc(BaseModel):
    path: str                       # relative to user_docs/
    markdown: str
    sections: list[Section] = Field(default_factory=list)


def slugify(t: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", t.lower()).strip("-")
    return s or "section"


def add_anchors(md: str) -> tuple[str, list[Section]]:
    """Append `{#slug}` to headings lacking an anchor; collect sections."""
    out, secs, used = [], [], set()
    in_fence = False
    for line in md.split("\n"):
        if line.lstrip().startswith("```"):
            in_fence = not in_fence
        m = None if in_fence else _HEAD.match(line)
        if m:
            level, title, anchor = len(m.group(1)), m.group(2), m.group(3)
            if anchor:
                a = anchor[2:-1]
            else:
                base = slugify(title)
                a, i = base, 2
                while a in used:
                    a, i = f"{base}-{i}", i + 1
                line = f"{line.rstrip()} {{#{a}}}"
            used.add(a)
            secs.append(Section(anchor=a, title=title, level=level))
        out.append(line)
    return "\n".join(out), secs


class _H2M(HTMLParser):
    def __init__(self):
        super().__init__()
        self.out: list[str] = []
        self.skip = 0

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style"):
            self.skip += 1
        elif re.fullmatch(r"h[1-6]", tag):
            self.out.append("\n\n" + "#" * int(tag[1]) + " ")
        elif tag in ("p", "div", "br", "tr", "ul", "ol", "table"):
            self.out.append("\n")
        elif tag == "li":
            self.out.append("\n- ")

    def handle_endtag(self, tag):
        if tag in ("script", "style"):
            self.skip = max(0, self.skip - 1)
        elif re.fullmatch(r"h[1-6]", tag) or tag in ("p", "div"):
            self.out.append("\n")

    def handle_data(self, data):
        if not self.skip:
            self.out.append(data)


def html_to_md(src: str) -> str:
    p = _H2M()
    p.feed(src)
    text = _html.unescape("".join(p.out))
    return re.sub(r"\n{3,}", "\n\n", text).strip() + "\n"


_W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"


def docx_to_md(path: Path) -> str:
    with zipfile.ZipFile(path) as z:
        root = ET.fromstring(z.read("word/document.xml"))
    lines = []
    for p in root.iter(_W + "p"):
        text = "".join(t.text or "" for t in p.iter(_W + "t")).strip()
        if not text:
            continue
        style = p.find(f"{_W}pPr/{_W}pStyle")
        sval = (style.get(_W + "val") if style is not None else "") or ""
        m = re.fullmatch(r"(?i)heading\s*(\d)", sval)
        if m:
            lines.append("#" * min(6, max(1, int(m.group(1)))) + " " + text)
        elif sval.lower() == "title":
            lines.append("# " + text)
        elif sval.lower().startswith("listparagraph"):
            lines.append("- " + text)
        else:
            lines.append(text)
    return "\n\n".join(lines) + "\n"


def pdf_to_md(path: Path) -> str | None:
    try:
        from pypdf import PdfReader  # type: ignore
    except ImportError:
        log.warning("skipping %s: pypdf is not installed", path.name)
        return None
    return "\n\n".join((pg.extract_text() or "") for pg in PdfReader(str(path)).pages) + "\n"


def convert_file(path: Path) -> str | None:
    ext = path.suffix.lower()
    if ext in (".md", ".txt"):
        return path.read_text(encoding="utf-8", errors="replace")
    if ext in (".html", ".htm"):
        return html_to_md(path.read_text(encoding="utf-8", errors="replace"))
    if ext == ".docx":
        return docx_to_md(path)
    if ext == ".pdf":
        return pdf_to_md(path)
    return None


def load_user_docs(project) -> list[UserDoc]:
    base = Path(project.root) / "user_docs"
    if not base.is_dir():
        return []
    docs: list[UserDoc] = []
    for f in sorted(p for p in base.rglob("*") if p.is_file() and not p.name.startswith(".")):
        if f.suffix.lower() not in SUPPORTED:
            log.warning("skipping unsupported user doc %s", f.name)
            continue
        try:
            md = convert_file(f)
        except Exception as e:  # corrupt docx etc.
            log.warning("skipping %s: %s", f.name, e)
            continue
        if md is None:
            continue
        md, secs = add_anchors(md.replace("\r\n", "\n"))
        docs.append(UserDoc(path=f.relative_to(base).as_posix(), markdown=md, sections=secs))
    return docs
