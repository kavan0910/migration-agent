import zipfile

from migration_agent.project import Project
from migration_agent.verify.userdocs import load_user_docs

DOCX_XML = ('<?xml version="1.0"?><w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body>'
            '<w:p><w:pPr><w:pStyle w:val="Heading1"/></w:pPr><w:r><w:t>Billing Rules</w:t></w:r></w:p>'
            '<w:p><w:r><w:t>Invoices are due in </w:t></w:r><w:r><w:t>30 days.</w:t></w:r></w:p>'
            '</w:body></w:document>')


def test_conversion(tmp_path):
    p = Project.init(tmp_path / "p", source="x")
    ud = p.root / "user_docs"
    (ud / "a.md").write_text("# Intro\ntext\n## Intro\nmore\n")
    (ud / "b.txt").write_text("plain")
    (ud / "c.html").write_text("<h2>Limits</h2><p>Max 5</p><script>bad()</script>")
    (ud / "d.pdf").write_bytes(b"%PDF")
    (ud / "e.bin").write_bytes(b"x")
    with zipfile.ZipFile(ud / "f.docx", "w") as z:
        z.writestr("word/document.xml", DOCX_XML)
    docs = {d.path: d for d in load_user_docs(p)}
    assert "{#intro}" in docs["a.md"].markdown and "{#intro-2}" in docs["a.md"].markdown
    assert [s.anchor for s in docs["a.md"].sections] == ["intro", "intro-2"]
    assert "## Limits {#limits}" in docs["c.html"].markdown and "bad()" not in docs["c.html"].markdown
    assert "# Billing Rules {#billing-rules}" in docs["f.docx"].markdown
    assert "Invoices are due in 30 days." in docs["f.docx"].markdown
    assert "e.bin" not in docs
