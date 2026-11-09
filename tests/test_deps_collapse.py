from migration_agent.analyze.merger import collapse_entity_components
from migration_agent.extract.deps import extract_deps
from migration_agent.graph.models import Edge, Graph, Node


def w(root, rel, text):
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text)


def test_js_require_and_import(tmp_path):
    w(tmp_path, "src/a.js", "const b = require('./b');\nimport c from '../lib/c'\n")
    w(tmp_path, "src/b.js", "")
    w(tmp_path, "lib/c/index.js", "")
    files = ["src/a.js", "src/b.js", "lib/c/index.js"]
    assert extract_deps(tmp_path, files) == [("src/a.js", "lib/c/index.js", 2), ("src/a.js", "src/b.js", 1)]


def test_python_relative_imports(tmp_path):
    w(tmp_path, "pkg/views.py", "from . import services\nfrom .models import X\nimport os\n")
    w(tmp_path, "pkg/services.py", "")
    w(tmp_path, "pkg/models.py", "")
    files = ["pkg/views.py", "pkg/services.py", "pkg/models.py"]
    assert extract_deps(tmp_path, files) == [("pkg/views.py", "pkg/models.py", 2), ("pkg/views.py", "pkg/services.py", 1)]


def test_java_same_package_and_comments(tmp_path):
    d = "src/com/x/"
    w(tmp_path, d + "Svc.java", "package com.x;\nclass Svc { Book b; // Member only in comment\n}\n")
    w(tmp_path, d + "Book.java", "package com.x;\nclass Book {}\n")
    w(tmp_path, d + "Member.java", "package com.x;\nclass Member {}\n")
    files = [d + "Svc.java", d + "Book.java", d + "Member.java"]
    assert extract_deps(tmp_path, files) == [(d + "Svc.java", d + "Book.java", 2)]


def test_collapse_entity_components():
    n = lambda i, t, m: Node(id=i, type=t, name=i, module=m, qualified_symbol=m, inventory_refs=[f"INV-{i}"])
    g = Graph(nodes=[n("COMP-0001", "component", "a.py"), n("COMP-0002", "component", "m.py"),
                     n("ENT-0001", "data_entity", "m.py")],
              edges=[Edge(id="EDGE-0001", source="COMP-0001", target="COMP-0002", type="calls"),
                     Edge(id="EDGE-0002", source="COMP-0001", target="ENT-0001", type="depends_on")])
    out = collapse_entity_components(g)
    assert [x.id for x in out.nodes] == ["COMP-0001", "ENT-0001"]
    assert set(next(x for x in out.nodes if x.id == "ENT-0001").inventory_refs) == {"INV-COMP-0002", "INV-ENT-0001"}
    assert [(e.source, e.type, e.target) for e in out.edges] == [("COMP-0001", "depends_on", "ENT-0001")]
