import random
import shutil
from pathlib import Path

from migration_agent.analyze.chunker import plan_chunks, render_chunk_text
from migration_agent.extract.treesitter import extract_ast

REPO = Path(__file__).resolve().parent.parent / "eval" / "repos" / "express-crud"


def write(root: Path, files: dict[str, str]) -> Path:
    for rel, body in files.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(body)
    return root


def plan(root, **kw):
    return plan_chunks(root, extract_ast(root), **kw)


def small_budget(tokens):
    # budget = tokens exactly
    return dict(context_tokens=tokens * 2, budget_fraction=0.5, reserve_tokens=0)


def py_func(name, n=20):
    return f"def {name}():\n" + "".join(f"    x{i} = {i}\n" for i in range(n)) + "\n"


def test_small_repo_one_chunk(tmp_path):
    write(tmp_path, {"a.py": "import b\n", "b.py": "x = 1\n", "pkg/c.js": "let a = 1\n"})
    cs = plan(tmp_path)
    assert len(cs) == 1 and cs[0].id == "CH-0001" and cs[0].order == 1
    assert sorted(cs[0].files) == ["a.py", "b.py", "pkg/c.js"]


def test_big_repo_splits_by_directory(tmp_path):
    files = {}
    for d in ("alpha", "beta", "gamma"):
        for i in range(3):
            files[f"{d}/m{i}.py"] = py_func(f"f{i}", 30)
    write(tmp_path, files)
    cs = plan(tmp_path, **small_budget(1000))
    assert len(cs) >= 3
    for c in cs:
        assert len({Path(f).parent for f in c.files}) == 1  # directory kept together
        assert c.est_tokens <= 1000
    assert sorted(f for c in cs for f in c.files) == sorted(files)


def test_huge_file_split(tmp_path):
    src = "".join(py_func(f"fn{i}", 40) for i in range(10))
    write(tmp_path, {"big.py": src, "small.py": "x = 1\n"})
    cs = plan(tmp_path, **small_budget(800))
    big = [c for c in cs if "big.py" in c.files]
    assert len(big) > 1 and all(c.partial_file for c in big)
    covered = sorted(r for c in big for r in c.ranges["big.py"])
    n = len(src.splitlines())
    assert covered[0][0] == 1 and covered[-1][1] == n
    for (a1, b1), (a2, b2) in zip(covered, covered[1:]):
        assert a2 == b1 + 1
    # split on symbol boundaries: every non-first range starts at a def
    lines = src.splitlines()
    for a, _ in covered[1:]:
        assert lines[a - 1].startswith("def ")
    assert all(c.est_tokens <= 800 for c in cs)
    txt = render_chunk_text(tmp_path, big[0])
    assert txt.startswith(f"=== big.py (lines 1-{covered[0][1]}) ===")


def test_leaves_first(tmp_path):
    write(tmp_path, {
        "app/main.py": "from lib import util\nimport core.db\n",
        "lib/util.py": "from core import db\n",
        "core/db.py": "x = 1\n",
    })
    cs = plan(tmp_path, **small_budget(20))
    order = [c.files[0] for c in cs]
    assert order.index("core/db.py") < order.index("lib/util.py") < order.index("app/main.py")
    byfile = {c.files[0]: c for c in cs}
    assert byfile["core/db.py"].id in byfile["lib/util.py"].depends_on_chunks
    for c in cs:
        assert all(d < c.id for d in c.depends_on_chunks)


def test_js_relative_and_require(tmp_path):
    write(tmp_path, {
        "src/a.js": "const b = require('./lib/b')\nimport c from './c'\n",
        "src/lib/b.js": "module.exports = 1\n",
        "src/c.ts": "export default 1\n",
    })
    cs = plan(tmp_path, **small_budget(20))
    order = [c.files[0] for c in cs]
    assert order[-1] == "src/a.js"


def test_cycles(tmp_path):
    write(tmp_path, {
        "x/a.py": "import y.b\n",
        "y/b.py": "import x.a\n",
        "z/leaf.py": "v = 1\n",
        "w/top.py": "import x.a\nimport z.leaf\n",
    })
    cs = plan(tmp_path, **small_budget(20))
    assert len(cs) == 4
    order = [c.files[0] for c in cs]
    assert order[0] in ("x/a.py", "z/leaf.py")
    assert order[-1] == "w/top.py"
    assert order.index("z/leaf.py") < order.index("w/top.py")
    for c in cs:
        assert all(d < c.id for d in c.depends_on_chunks)


def test_java_go_and_fallback(tmp_path):
    write(tmp_path, {
        "src/com/acme/Main.java": "package com.acme;\nimport com.acme.util.Helper;\nclass Main {}\n",
        "src/com/acme/util/Helper.java": "package com.acme.util;\nclass Helper {}\n",
        "g/cmd/main.go": 'package main\nimport "example.com/app/pkg/store"\nfunc main() {}\n',
        "g/pkg/store/s.go": "package store\nfunc S() {}\n",
        "legacy/PAY.cbl": "       IDENTIFICATION DIVISION.\n",
    })
    cs = plan(tmp_path, **small_budget(40))
    order = [c.files[0] for c in cs]
    assert order.index("src/com/acme/util/Helper.java") < order.index("src/com/acme/Main.java")
    assert order.index("g/pkg/store/s.go") < order.index("g/cmd/main.go")
    assert "legacy/PAY.cbl" in order


def test_determinism(tmp_path):
    files = {f"d{i % 4}/f{i}.py": f"import d{(i + 1) % 4}.f{(i + 1)}\n" + py_func("f", 15)
             for i in range(12)}
    a = tmp_path / "a"
    b = tmp_path / "b"
    write(a, files)
    items = list(files.items())
    random.Random(7).shuffle(items)
    write(b, dict(items))
    ca = plan(a, **small_budget(500))
    cb = plan(b, **small_budget(500))
    assert [c.model_dump() for c in ca] == [c.model_dump() for c in cb]
    asts = extract_ast(a)
    asts.files.reverse()
    assert [c.model_dump() for c in plan_chunks(a, asts, **small_budget(500))] == \
        [c.model_dump() for c in ca]


def test_budget_never_exceeded_and_render_matches(tmp_path):
    files = {f"p{i % 3}/f{i}.py": py_func("g", 10 + i * 7) for i in range(15)}
    write(tmp_path, files)
    budget = 400
    cs = plan(tmp_path, **small_budget(budget))
    assert cs
    for c in cs:
        assert c.est_tokens <= budget
        text = render_chunk_text(tmp_path, c)
        assert len(text) / 4 <= budget
        assert "=== " in text


def test_render_line_numbers(tmp_path):
    write(tmp_path, {"a.py": "x = 1\ny = 2\n"})
    c = plan(tmp_path)[0]
    t = render_chunk_text(tmp_path, c)
    assert t.splitlines() == ["=== a.py (lines 1-2) ===", "    1| x = 1", "    2| y = 2"]


def test_express_crud():
    ast = extract_ast(REPO)
    cs = plan_chunks(REPO, ast)
    assert len(cs) == 1  # small repo fits one chunk
    assert len(cs[0].files) == len(ast.files)
    tiny = plan_chunks(REPO, ast, **small_budget(900))
    assert len(tiny) > 1
    pos = {f: c.order for c in tiny for f in c.files}
    assert pos["src/models/db.js"] <= pos["src/models/product.js"]
    assert pos["src/models/product.js"] <= pos["src/services/productService.js"]
    assert pos["src/services/productService.js"] <= pos["src/routes/products.js"]
    assert pos["src/routes/index.js"] <= pos["src/app.js"] <= pos["src/server.js"]
    assert all(c.est_tokens <= 900 for c in tiny)
    assert "src/app.js (lines" in "".join(render_chunk_text(REPO, c) for c in tiny)
