from helpers import facts_for, gradle_project

from crgraph.config import Config
from crgraph.expand import changed_symbol_names
from crgraph.gitio import Repo
from crgraph.model import Status, file_id, module_id, sym_id
from crgraph.pipeline import run

REPO = """
    package com.example.core
    class Repo {
        fun load(id: Int): Int = id
    }
"""


def project():
    return {
        **gradle_project({":core": [], ":app": [":core"], ":widget": [":core"], ":misc": []}),
        "core/src/com/example/core/Repo.kt": REPO,
        "app/src/com/example/app/Screen.kt": """
            package com.example.app
            import com.example.core.Repo
            class Screen { fun show() = Repo().load(1) }
        """,
        "widget/src/com/example/widget/Card.kt": """
            package com.example.widget
            import com.example.core.Repo
            class Card { fun render() = Repo().load(2) }
        """,
        "misc/src/com/example/misc/Note.kt": "package com.example.misc\nclass Note\n",
    }


def test_changed_symbol_names():
    before = facts_for({"a/A.kt": "package a\nclass Keep\nclass Repo { fun load() = 1 }\nprivate fun hidden() = 1\n"})
    after = facts_for({"a/A.kt": "package a\nclass Keep\nclass Repo { fun load() = 2 }\nprivate fun hidden() = 2\nfun ab() = 1\n"})
    assert changed_symbol_names(before, after, {}) == {"Repo"}  # 'ab' is shorter than 3, hidden is private


def test_pipeline_finds_impacted_dependents(repo):
    base = repo.commit(project())
    head = repo.commit({"core/src/com/example/core/Repo.kt": REPO.replace("id: Int", "id: Long"),
                        "app/src/com/example/app/New.kt": "package com.example.app\nclass New\n"})
    r = Repo(repo.root)
    result = run(r, r.resolve_range([base, head]), Config())
    nodes = result.delta.nodes
    assert nodes[sym_id("core/src/com/example/core/Repo.kt", "Repo.load", 1)].status == Status.MODIFIED
    assert nodes[file_id("app/src/com/example/app/New.kt")].status == Status.ADDED
    assert nodes[file_id("widget/src/com/example/widget/Card.kt")].status == Status.IMPACTED
    assert nodes[file_id("app/src/com/example/app/Screen.kt")].status == Status.IMPACTED
    assert nodes[module_id(":widget")].status == Status.IMPACTED
    assert nodes[module_id(":misc")].status == Status.UNCHANGED
    assert file_id("misc/src/com/example/misc/Note.kt") not in nodes
    assert result.warnings == []


def test_pipeline_pathspec_and_empty_diff(repo):
    base = repo.commit(project())
    head = repo.commit({"misc/src/com/example/misc/Note.kt": "package com.example.misc\nclass Note2\n"})
    r = Repo(repo.root)
    filtered = run(r, r.resolve_range([base, head]), Config(), ["core/"])
    assert filtered.changes == []
    empty = run(r, r.resolve_range([head, head]), Config())
    assert empty.changes == [] and empty.findings == []
    assert all(n.status == Status.UNCHANGED for n in empty.delta.nodes.values())
