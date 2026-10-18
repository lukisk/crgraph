from helpers import findings, gradle_project, run_range

from crgraph.config import Config
from crgraph.model import Severity

REPO = """
    package com.example.core
    class Repo {
        fun load(id: Int): Int = id
        fun legacy(): Int = 0
    }
"""
SCREEN = """
    package com.example.app
    import com.example.core.Repo
    class Screen { fun show() = Repo().load(1) }
"""


def project():
    return {**gradle_project({":core": [], ":app": [":core"]}),
            "core/src/com/example/core/Repo.kt": REPO, "app/src/com/example/app/Screen.kt": SCREEN}


def test_api_change_used_from_other_module_is_red(repo):
    base = repo.commit(project())
    head = repo.commit({"core/src/com/example/core/Repo.kt": REPO.replace("load(id: Int)", "load(id: Long)")})
    api = findings(run_range(repo, base, head), "api-change")
    assert [(f.severity, f.title) for f in api] == [(Severity.RED, "Public API signature changed: Repo.load")]
    assert "before: fun load(id: Int): Int" in api[0].detail and "after:  fun load(id: Long): Int" in api[0].detail


def test_removed_unused_api_is_yellow(repo):
    base = repo.commit(project())
    head = repo.commit({"core/src/com/example/core/Repo.kt": REPO.replace("        fun legacy(): Int = 0\n", "")})
    api = findings(run_range(repo, base, head), "api-change")
    assert [(f.severity, f.title) for f in api] == [(Severity.YELLOW, "Public API removed: Repo.legacy")]


def test_visibility_reduction_is_api_change(repo):
    base = repo.commit(project())
    head = repo.commit({"core/src/com/example/core/Repo.kt": REPO.replace("fun legacy()", "internal fun legacy()")})
    assert [f.title for f in findings(run_range(repo, base, head), "api-change")] == [
        "Public API visibility reduced to internal: Repo.legacy"]


def test_blast_radius(repo):
    base = repo.commit(project())
    head = repo.commit({"core/src/com/example/core/Repo.kt": REPO.replace("= id", "= id + 1")})
    blast = findings(run_range(repo, base, head), "blast-radius")
    assert [(f.severity, f.title) for f in blast] == [
        (Severity.INFO, "1 unchanged file(s) in 1 other module(s) use changed code")]
    assert "Impacted modules: :app" in blast[0].detail


def test_complexity_increase(repo):
    simple = "def f(x):\n    return x\n"
    branchy = "def f(x):\n" + "".join(f"    if x == {i}:\n        return {i}\n" for i in range(6)) + "    return x\n"
    base = repo.commit({"m.py": simple})
    head = repo.commit({"m.py": branchy})
    assert [f.title for f in findings(run_range(repo, base, head), "complexity")] == ["Complexity of f 1 → 7"]
    cfg = Config()
    cfg.thresholds.complexity_max = 3
    head2 = repo.commit({"n.py": branchy.replace("def f", "def g")})
    assert [f.title for f in findings(run_range(repo, head, head2, cfg), "complexity")] == [
        "New function g has complexity 7"]


def test_dead_on_arrival(repo):
    base = repo.commit(project())
    head = repo.commit({"core/src/com/example/core/Extra.kt": "package com.example.core\nclass Unused\nfun helper() = 1\n",
                        "core/src/com/example/core/Used.kt": "package com.example.core\nclass Used\n",
                        "app/src/com/example/app/Screen.kt": SCREEN.replace("Repo().load(1)", "Repo().load(1) + Used().hashCode()")})
    doa = findings(run_range(repo, base, head), "dead-on-arrival")
    assert len(doa) == 1
    assert doa[0].title == "2 new public symbol(s) in :core have no callers in the analysed code"
    assert doa[0].detail == "Unused, helper"


def test_removed_class_is_one_finding_and_tests_are_not_api(repo):
    base = repo.commit({**project(),
                        "core/src/com/example/core/Gone.kt": "package com.example.core\nclass Gone {\n    fun a() = 1\n    fun b() = 2\n}\n",
                        "core/src/test/com/example/core/RepoTest.kt": "package com.example.core\nclass RepoTest { fun `loads (1)`() = 1 }\n"})
    head = repo.commit(delete=("core/src/com/example/core/Gone.kt", "core/src/test/com/example/core/RepoTest.kt"))
    assert [f.title for f in findings(run_range(repo, base, head), "api-change")] == ["Public API removed: Gone"]


def test_constant_value_change_is_not_a_signature_change(repo):
    base = repo.commit({"core/src/C.kt": "package c\nobject C { const val VERSION = 17 }\n"})
    head = repo.commit({"core/src/C.kt": "package c\nobject C { const val VERSION = 18 }\n"})
    assert findings(run_range(repo, base, head), "api-change") == []


def test_complexity_noise_on_already_complex_function_is_ignored(repo):
    def body(n):
        return "def f(x):\n" + "".join(f"    if x == {i}:\n        return {i}\n" for i in range(n)) + "    return x\n"
    base = repo.commit({"m.py": body(20)})
    head = repo.commit({"m.py": body(21)})
    assert findings(run_range(repo, base, head), "complexity") == []


def test_added_parameter_is_a_signature_change_not_a_removal(repo):
    base = repo.commit(project())
    head = repo.commit({"core/src/com/example/core/Repo.kt": REPO.replace("load(id: Int)", "load(id: Int, cache: Boolean)"),
                        "app/src/com/example/app/Screen.kt": SCREEN.replace("load(1)", "load(1, true)")})
    result = run_range(repo, base, head)
    api = findings(result, "api-change")
    assert [(f.severity, f.title) for f in api] == [(Severity.RED, "Public API signature changed: Repo.load")]
    assert "after:  fun load(id: Int, cache: Boolean): Int" in api[0].detail
    loads = [n for n in result.delta.nodes.values() if n.name == "load"]
    assert [(n.id.rpartition("#")[2], n.status) for n in loads] == [("Repo.load(2)", "modified")]
    assert not [e for e in result.delta.edges if e.src not in result.delta.nodes or e.dst not in result.delta.nodes]
