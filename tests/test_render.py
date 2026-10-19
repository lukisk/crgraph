import json

from helpers import gradle_project, run_range

from crgraph.render.jsonout import to_document, write_json
from crgraph.render.markdown import MAX_NODES, mermaid, to_markdown

REPO = "package com.example.core\nclass Repo {\n    fun load(id: Int): Int {\n        val secret = \"s3cr3t\"\n        return id\n    }\n}\n"


def result(repo):
    base = repo.commit({**gradle_project({":core": [], ":app": [":core"]}), "core/src/Repo.kt": REPO,
                        "app/src/Screen.kt": "package com.example.app\nimport com.example.core.Repo\nclass Screen { fun show() = Repo().load(1) }\n"})
    head = repo.commit({**gradle_project({":core": [":app"], ":app": [":core"]}),
                        "core/src/Repo.kt": REPO.replace("id: Int", "id: Long")})
    return run_range(repo, base, head)


def python_with_tests(repo):
    test = "from pkg.calc import add\n\n\ndef test_add():\n    assert add({}) == 3\n"
    base = repo.commit({"pkg/calc.py": "def add(a, b):\n    return a + b\n", "tests/test_calc.py": test.format("1, 2")})
    head = repo.commit({"pkg/calc.py": "def add(a, b):\n    return b + a\n", "tests/test_calc.py": test.format("2, 1")})
    return run_range(repo, base, head)


def test_document_shape_and_no_source_text(repo, tmp_path):
    doc = to_document(result(repo), depth="file", explain="Short summary.")
    assert doc["schema_version"] == 1 and doc["ui"] == {"depth": "file"} and doc["explain"] == "Short summary."
    load = next(n for n in doc["nodes"] if n["id"].endswith("#Repo.load(1)"))
    assert load["status"] == "modified"
    assert load["signature_before"] == "fun load(id: Int): Int" and load["signature_after"] == "fun load(id: Long): Int"
    assert doc["stats"]["findings"]["red"] >= 1
    assert all(f["id"].startswith("F") for f in doc["findings"])
    path = tmp_path / "delta.json"
    write_json(doc, path)
    text = path.read_text()
    assert "s3cr3t" not in text and "return id" not in text  # never any code body
    assert json.loads(text)["range"]["label"] == doc["range"]["label"]


def test_document_marks_test_modules_files_and_symbols(repo):
    tests = {n["id"] for n in to_document(python_with_tests(repo))["nodes"] if n["test"]}
    assert tests == {"module:tests", "file:tests/test_calc.py", "sym:tests/test_calc.py#test_add(0)"}


def test_markdown_has_mermaid_styles_and_findings(repo):
    md = to_markdown(to_document(result(repo)))
    assert "```mermaid" in md and "flowchart LR" in md
    assert "classDef modified" in md and "linkStyle" in md  # the new cycle's edge is red
    assert "| 🔴 | new-cycle |" in md
    assert "```\n_Legend: A → B means A depends on B. Thick arrow: new dependency" in md
    assert "s3cr3t" not in md


def test_markdown_caps_nodes():
    nodes = [{"id": f"module::m{i}", "kind": "module", "name": f":m{i}", "status": "modified"} for i in range(40)]
    doc = {"range": {"label": "a..b"}, "stats": {"modules": {"modified": 40}, "files": {}, "symbols": {}, "findings": {}},
           "nodes": nodes, "edges": [], "findings": [], "warnings": ["w|1"], "explain": None}
    md = to_markdown(doc)
    assert md.count(":::modified") == MAX_NODES and '+10 more modules' in md
    assert "No findings." in md and "w\\|1" in md


def test_document_drops_unchanged_noise(repo):
    base = repo.commit({"a.py": "def keep():\n    return 1\n\ndef other():\n    return keep()\n\ndef changed():\n    return 1\n"})
    head = repo.commit({"a.py": "def keep():\n    return 1\n\ndef other():\n    return keep()\n\ndef changed():\n    return 2\n"})
    doc = to_document(run_range(repo, base, head))
    ids = {n["id"] for n in doc["nodes"]}
    assert "sym:a.py#changed(0)" in ids and "file:a.py" in ids
    assert "sym:a.py#keep(0)" not in ids and "sym:a.py#other(0)" not in ids
    assert all("id" not in e for e in doc["edges"])


def test_document_and_markdown_include_the_overview(repo):
    doc = to_document(result(repo))
    ov = doc["overview"]
    assert ov["size_label"] == "S" and ov["verdict"].startswith("Mostly changes to existing code")
    md = to_markdown(doc)
    assert md.index("### Overview") < md.index("```mermaid")
    assert f"**Size: S** · {ov['verdict']}" in md
    assert "**Lines:** code +1 −1 · no test changes · other +1 −0" in md
    assert "| Module | Status | Files | Symbols | Code lines | Test lines | Findings | Used by |" in md
    assert "| :core | modified | 2 | ~2 | +1 −1 | — | 🔴 2 🟡 2 | 1 |" in md
    assert "**Start here**" in md and "1. `core/src/Repo.kt` — " in md


def test_markdown_caps_overview_rows():
    row = {"id": "module::m", "status": "modified", "files": 1, "symbols": {"added": 1, "removed": 0, "modified": 0, "moved": 0},
           "lines": {"added": 1, "deleted": 0}, "test_lines": {"added": 0, "deleted": 0},
           "findings": {"red": 0, "yellow": 0}, "used_by": 0}
    overview = {"verdict": "Mostly new code across 20 modules.", "size_label": "L", "start_here": [],
                "size": {"code": {"added": 20, "deleted": 0}, "tests": {"added": 0, "deleted": 0},
                         "other": {"added": 0, "deleted": 0}, "binary_files": 0, "test_share": 0},
                "modules": [{**row, "name": f":m{i}"} for i in range(20)], "other_modules": [f"d{i}" for i in range(7)]}
    doc = {"range": {"label": "a..b"}, "stats": {"modules": {}, "files": {}, "symbols": {}, "findings": {}},
           "nodes": [], "edges": [], "findings": [], "warnings": [], "explain": None, "overview": overview}
    md = to_markdown(doc)
    assert md.count("| modified | 1 | +1 |") == 15 and "+5 more modules" in md
    assert "Only docs/config changed in 7 more: d0, d1, d2, d3, d4, …" in md


def test_overview_paths_and_verdict_cannot_inject_markdown():
    evil = "a`b\n\n## Approved ✅\n[x](https://evil)`.kt"
    overview = {"verdict": "Mostly new code in :x\n\n# Fake heading.", "size_label": "S", "modules": [],
                "other_modules": [], "start_here": [{"path": evil, "reasons": ["1 line(s) changed\n# hi"]}],
                "size": {"code": {"added": 1, "deleted": 0}, "tests": {"added": 0, "deleted": 0},
                         "other": {"added": 0, "deleted": 0}, "binary_files": 0, "test_share": 0}}
    doc = {"range": {"label": "a..b"}, "stats": {"modules": {}, "files": {}, "symbols": {}, "findings": {}},
           "nodes": [], "edges": [], "findings": [], "warnings": [], "explain": None, "overview": overview}
    md = to_markdown(doc)
    assert not [line for line in md.splitlines() if line.startswith(("#", "[x]")) and not line.startswith("## crgraph")
                and not line.startswith("### Overview")]
    assert "1. ``a`b  ## Approved ✅ [x](https://evil)`.kt`` — " in md


def test_mermaid_labels_cannot_close_the_fence():
    nodes = [{"id": "module::x", "kind": "module", "name": "x\n```\n# Injected", "status": "modified"}]
    doc = {"range": {"label": "a..b"}, "stats": {"modules": {"modified": 1}, "files": {}, "symbols": {}, "findings": {}},
           "nodes": nodes, "edges": [], "findings": [], "warnings": [], "explain": None}
    md = to_markdown(doc)
    assert md.count("```") == 2 and "\n# Injected" not in md


def test_mermaid_groups_siblings_and_collapses_their_shared_dependency():
    names = [":lib:core", ":lib:feature:a", ":lib:feature:b", ":lib:feature:c"]
    nodes = [{"id": f"module::{n}", "kind": "module", "name": n, "status": "modified"} for n in names]
    edges = [{"kind": "depends_on", "src": f"module::{n}", "dst": "module:::lib:core", "status": "unchanged"}
             for n in names[1:]]
    edges.append({"kind": "depends_on", "src": "module:::lib:feature:a", "dst": "module:::lib:feature:b",
                  "status": "added"})
    lines = mermaid({"nodes": nodes, "edges": edges, "findings": []})
    assert '  subgraph g0[":lib:feature"]' in lines and '    m1("a"):::modified' in lines
    assert "  g0 --> m0" in lines and "  m1 ==> m2" in lines
    assert not [line for line in lines if line.endswith("--> m0") and line != "  g0 --> m0"]
    assert all("color:" in line for line in lines if line.lstrip().startswith("classDef"))


def test_markdown_cells_cannot_inject_links_images_or_html():
    from crgraph.render.markdown import _cell
    out = _cell("![x](https://evil/p.png) [y](javascript:z) <img src=q> **b** a|b\nc")
    assert out == "\\!\\[x\\](https://evil/p.png) \\[y\\](javascript:z) &lt;img src=q&gt; \\*\\*b\\*\\* a\\|b c"


def test_markdown_shows_the_review_as_a_verdict_and_table():
    base = {"range": {"label": "a..b"}, "stats": {"modules": {}, "files": {}, "symbols": {}, "findings": {}},
            "nodes": [], "edges": [], "findings": [], "warnings": []}
    md = to_markdown({**base, "explain": "x", "review": {"verdict": "Acceptable", "reason": "small | safe",
                                                          "items": [["DRY", "no *duplication*"]]}})
    assert "**AI review: 🟡 Acceptable** — small \\| safe" in md
    assert "| Aspect | Assessment |" in md and "| DRY | no \\*duplication\\* |" in md
    plain = to_markdown({**base, "explain": "Free text.", "review": None})
    assert "Free text." in plain and "Aspect" not in plain
