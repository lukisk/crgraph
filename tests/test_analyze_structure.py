from helpers import findings, gradle_project, run_range

from crgraph.config import Config, ForbidRule, LayersRule
from crgraph.graphalg import strongly_connected
from crgraph.model import Severity, module_id


def test_strongly_connected():
    comps = strongly_connected([("a", "b"), ("b", "c"), ("c", "a"), ("c", "d"), ("d", "e")])
    assert comps == [{"a", "b", "c"}]
    assert strongly_connected([("x", "y")]) == []


def test_new_module_cycle_is_red_and_existing_cycle_is_not(repo):
    base = repo.commit(gradle_project({":a": [":b"], ":b": [], ":c": [":d"], ":d": [":c"]}))
    head = repo.commit(gradle_project({":a": [":b"], ":b": [":a"], ":c": [":d"], ":d": [":c"]}))
    cycles = findings(run_range(repo, base, head), "new-cycle")
    assert len(cycles) == 1
    assert cycles[0].severity == Severity.RED
    assert cycles[0].title == "New module cycle: :b → :a → :b"
    assert module_id(":a") in cycles[0].targets and f"{module_id(':b')}->{module_id(':a')}:depends_on" in cycles[0].targets


def test_new_file_cycle_from_imports(repo):
    files = {
        "pkg/a.py": "from .b import g\ndef f():\n    return g()\n",
        "pkg/b.py": "def g():\n    return 1\n",
    }
    base = repo.commit(files)
    head = repo.commit({"pkg/b.py": "from .a import f\ndef g():\n    return 1\n"})
    titles = [f.title for f in findings(run_range(repo, base, head), "new-cycle")]
    assert titles == ["New file cycle: pkg/b.py → pkg/a.py → pkg/b.py"]


def test_forbid_and_layer_rules(repo):
    base = repo.commit({
        **gradle_project({":platform:detail": [], ":shared:multiplatform": [], ":core": []}),
        "app/data/repo.py": "def load():\n    return 1\n",
        "app/ui/screen.py": "from app.data.repo import load\n",
        "app/__init__.py": "", "app/data/__init__.py": "", "app/ui/__init__.py": "",
    })
    head = repo.commit({
        **gradle_project({":platform:detail": [":shared:multiplatform"], ":shared:multiplatform": [], ":core": []}),
        "app/data/repo.py": "from app.ui.screen import show\ndef load():\n    return show()\n",
        "app/ui/screen.py": "from app.data.repo import load\ndef show():\n    return 1\n",
    })
    cfg = Config(rules=[ForbidRule("platform/**", "shared/multiplatform/**"),
                        LayersRule(("ui", "domain", "data"), "**/{layer}/**")])
    titles = sorted(f.title for f in findings(run_range(repo, base, head, cfg), "rule-violation"))
    assert titles == ["Rule violated: forbid platform/** -> shared/multiplatform/**",
                      "Rule violated: layers ui > domain > data"]


def test_module_dependency_changes_and_coupling(repo):
    base = repo.commit(gradle_project({":a": [":b"], ":b": [], ":c": [], ":d": [], ":e": []}))
    head = repo.commit(gradle_project({":a": [":c", ":d", ":e"], ":b": [], ":c": [], ":d": [], ":e": []}))
    result = run_range(repo, base, head)
    assert [f.title for f in findings(result, "module-dep-added")] == [
        "New dependency :a → :c", "New dependency :a → :d", "New dependency :a → :e"]
    assert [f.title for f in findings(result, "module-dep-removed")] == ["Removed dependency :a → :b"]
    assert [f.title for f in findings(result, "coupling-increase")] == []  # 1 -> 3 is +2 < 3
    cfg = Config()
    cfg.thresholds.fanout_delta = 2
    assert [f.title for f in findings(run_range(repo, base, head, cfg), "coupling-increase")] == [
        ":a now depends on 3 modules (was 1)"]


def test_splittable_change(repo):
    base = repo.commit({**gradle_project({":a": [], ":b": [], ":c": [":a"]}),
                        "a/A.kt": "package a\nclass A\n", "b/B.kt": "package b\nclass B\n", "c/C.kt": "package c\nclass C\n"})
    head = repo.commit({"a/A.kt": "package a\nclass A2\n", "b/B.kt": "package b\nclass B2\n", "c/C.kt": "package c\nclass C2\n"})
    split = findings(run_range(repo, base, head), "splittable")
    assert len(split) == 1 and split[0].title.startswith("Change touches 2 independent groups")
    assert "group 1: :a, :c" in split[0].detail and "group 2: :b" in split[0].detail


def test_same_package_dependency_is_not_a_new_file_cycle(repo):
    # B already used A without an import (same Kotlin package); moving A to another package adds imports
    base = repo.commit({
        "m/src/p/A.kt": "package p\nclass A { fun a() = B().b() }\n",
        "m/src/p/B.kt": "package p\nclass B { fun b() = A().a() }\n",
    })
    head = repo.commit({
        "m/src/p/A.kt": "package p\nimport q.B\nclass A { fun a() = B().b() }\n",
        "m/src/p/B.kt": "package q\nimport p.A\nclass B { fun b() = A().a() }\n",
    })
    assert findings(run_range(repo, base, head), "new-cycle") == []
