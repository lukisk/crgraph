from helpers import gradle_project, run_range

from crgraph.overview import size_label

REPO = """
    package com.example.core
    class Repo {
        fun load(id: Int): Int = id
        fun legacy(): Int = 0
    }
"""


def project():
    return {
        **gradle_project({":core": [], ":app": [":core"], ":widget": [":core"]}),
        "core/src/com/example/core/Repo.kt": REPO,
        "app/src/com/example/app/Screen.kt": "package com.example.app\nimport com.example.core.Repo\nclass Screen { fun show() = Repo().load(1) }\n",
        "widget/src/com/example/widget/Card.kt": "package com.example.widget\nimport com.example.core.Repo\nclass Card { fun render() = Repo().load(2) }\n",
    }


def test_overview_of_an_api_change_with_tests_and_docs(repo):
    base = repo.commit(project())
    head = repo.commit({
        "core/src/com/example/core/Repo.kt": REPO.replace("id: Int", "id: Long"),
        "core/src/test/com/example/core/RepoTest.kt": "package com.example.core\nclass RepoTest {\n    fun loads() = Repo().load(1)\n}\n",
        "README.md": "# demo\nnotes\n",
    })
    ov = run_range(repo, base, head).overview
    assert ov["size"]["code"] == {"added": 1, "deleted": 1}
    assert ov["size"]["tests"] == {"added": 4, "deleted": 0}
    assert ov["size"]["other"] == {"added": 2, "deleted": 0}
    assert ov["size"]["test_share"] == 80
    assert ov["size_label"] == "S"
    assert ov["verdict"] == "Mostly changes to existing code in :core; changes public API used by 2 other module(s)."
    core = ov["modules"][0]
    assert (core["name"], core["status"], core["files"], core["used_by"]) == (":core", "modified", 2, 2)
    assert core["symbols"] == {"added": 0, "removed": 0, "modified": 2, "moved": 0}
    assert core["lines"] == {"added": 1, "deleted": 1} and core["test_lines"] == {"added": 4, "deleted": 0}
    assert core["findings"] == {"red": 1, "yellow": 0}
    assert {m["name"]: m["status"] for m in ov["modules"][1:]} == {":app": "impacted", ":widget": "impacted"}
    assert ov["other_modules"] == ["(root)"]  # only docs/config changed there
    first = ov["start_here"][0]
    assert first["path"] == "core/src/com/example/core/Repo.kt"
    assert first["reasons"][:2] == ["1 red finding(s)", "used from 2 other file(s)"]
    assert all("Test" not in f["path"] for f in ov["start_here"])


def test_verdict_for_new_code_spread_over_modules(repo):
    base = repo.commit(project())
    head = repo.commit({
        "app/src/com/example/app/Feed.kt": "package com.example.app\nclass Feed {\n    fun a() = 1\n    fun b() = 2\n    fun c() = 3\n}\n",
        "widget/src/com/example/widget/Tile.kt": "package com.example.widget\nclass Tile {\n    fun d() = 4\n}\n",
    })
    ov = run_range(repo, base, head).overview
    assert ov["verdict"] == "Mostly new code in :app and :widget; touches 2 independent groups of modules."
    assert ov["size_label"] == "M"
    assert [f["path"] for f in ov["start_here"]] == ["app/src/com/example/app/Feed.kt", "widget/src/com/example/widget/Tile.kt"]


def test_verdict_for_moved_code(repo):
    base = repo.commit(project())
    repo.mv("core/src/com/example/core/Repo.kt", "core/src/com/example/core/data/Repo.kt")
    head = repo.commit()
    assert run_range(repo, base, head).overview["verdict"] == "Mostly moved code in :core."


def test_verdicts_for_tests_only_docs_only_and_empty(repo):
    base = repo.commit(project())
    tests = repo.commit({"core/src/test/com/example/core/RepoTest.kt": "package com.example.core\nclass RepoTest\n"})
    docs = repo.commit({"README.md": "hello\n"})
    only_tests = run_range(repo, base, tests).overview
    assert only_tests["verdict"] == "Tests only: 1 test file(s) changed."
    assert [f["path"] for f in only_tests["start_here"]] == ["core/src/test/com/example/core/RepoTest.kt"]
    assert run_range(repo, tests, docs).overview["verdict"] == "No code changes: only docs, config or other files."
    empty = run_range(repo, docs, docs).overview
    assert (empty["verdict"], empty["size_label"], empty["modules"], empty["start_here"]) == (
        "No changes.", None, [], [])


def test_start_here_lists_code_not_docs_scripts_or_tests(repo):
    base = repo.commit(project())
    head = repo.commit({
        "core/src/com/example/core/Repo.kt": REPO.replace("= id", "= id + 1"),
        "docs/adr/0001-decision.md": "# decision\n" + "text\n" * 300,
        "tools/sync.sh": "echo hi\n" * 200,
        "site/scripts/prepare.test.mjs": "test('x', () => {})\n" * 50,
    })
    ov = run_range(repo, base, head).overview
    assert [f["path"] for f in ov["start_here"]] == ["core/src/com/example/core/Repo.kt"]
    assert ov["size"]["tests"]["added"] == 50  # .mjs tests count as tests


def test_size_label_thresholds():
    assert size_label(199, 1) == "S"
    assert size_label(200, 1) == "M"
    assert size_label(10, 2) == "M"
    assert size_label(799, 3) == "M"
    assert size_label(10, 4) == "L"
    assert size_label(1999, 1) == "L"
    assert size_label(2000, 1) == "XL"
    assert size_label(5000, 9) == "XL"
