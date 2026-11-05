import json
import shutil
from pathlib import Path

from migration_agent.extract.treesitter import extract_ast, write_ast_facts

FIX = Path(__file__).parent / "fixtures" / "treesitter"


def get(facts, name):
    return next(f for f in facts.files if f.path == name)


def test_python():
    f = get(extract_ast(FIX), "sample.py")
    assert f.parse_status == "ok" and f.language == "python"
    assert [c.name for c in f.classes] == ["Foo"]
    assert f.classes[0].decorators == ["dataclass"]
    q = {x.qualified_name: x for x in f.functions}
    assert set(q) == {"Foo.bar", "top"}
    assert q["Foo.bar"].decorators == ["staticmethod"]
    assert q["Foo.bar"].params == ["a", "b=1", "*args", "**kw"]
    assert len(f.imports) == 2


def test_typescript_and_javascript():
    facts = extract_ast(FIX)
    for name, lang in (("sample.ts", "typescript"), ("sample.js", "javascript")):
        f = get(facts, name)
        assert f.language == lang
        assert f.classes[0].name == "Svc"
        q = {x.qualified_name: x for x in f.functions}
        assert {"Svc.handle", "f", "g"} <= set(q)
        assert f.imports == ['import { A } from "./a"']
    ts = get(facts, "sample.ts")
    assert ts.classes[0].decorators == ["Component"]
    assert [x for x in ts.functions if x.name == "handle"][0].decorators == ["Get"]


def test_java():
    f = get(extract_ast(FIX), "Sample.java")
    assert {c.name for c in f.classes} == {"Sample", "Iface"}
    assert [c for c in f.classes if c.name == "Sample"][0].decorators == ["Service"]
    q = {x.qualified_name: x for x in f.functions}
    assert q["Sample.run"].decorators == ["Override"]
    assert q["Sample.run"].params == ["String a", "int b"]
    assert "Sample.Sample" in q and "Iface.m" in q
    assert f.imports == ["import java.util.List"]


def test_go():
    f = get(extract_ast(FIX), "sample.go")
    assert {c.name: c.kind for c in f.classes} == {"Srv": "struct", "Doer": "interface"}
    q = {x.qualified_name for x in f.functions}
    assert q == {"Srv.Handle", "Main"}
    assert len(f.imports) == 2


def test_csharp():
    f = get(extract_ast(FIX), "Sample.cs")
    assert f.parse_status == "ok"
    cls = {c.name: c for c in f.classes}
    assert cls["Ctl"].decorators == ["ApiController"]
    q = {x.qualified_name: x for x in f.functions}
    assert q["N.Ctl.Get"].decorators == ["HttpGet"]
    assert q["N.Ctl.Get"].params == ["int a", "string b"]
    assert "N.IFoo.M" in q
    assert len(f.imports) == 2


def test_fallback_no_grammar():
    f = get(extract_ast(FIX), "legacy.cbl")
    assert f.parse_status == "fallback" and f.language == "cobol"
    assert f.line_count == 2 and not f.classes and not f.functions


def test_syntax_error_partial():
    f = get(extract_ast(FIX), "broken.py")
    assert f.parse_status == "partial"


def test_languages_deterministic_and_json(tmp_path):
    a, b = extract_ast(FIX), extract_ast(FIX)
    assert a == b
    assert [f.path for f in a.files] == sorted(f.path for f in a.files)
    assert a.languages["python"] == 2 and a.languages["cobol"] == 1
    out = tmp_path / "facts" / "ast.json"
    write_ast_facts(a, out)
    data = json.loads(out.read_text())
    assert set(data) == {"files", "languages"}
    assert out.read_text() == (write_ast_facts(b, tmp_path / "2.json") or (tmp_path / "2.json").read_text())


def test_skips_vendor_dirs(tmp_path):
    (tmp_path / "node_modules").mkdir()
    (tmp_path / "node_modules" / "x.js").write_text("function a(){}")
    shutil.copy(FIX / "sample.py", tmp_path / "m.py")
    assert [f.path for f in extract_ast(tmp_path).files] == ["m.py"]
