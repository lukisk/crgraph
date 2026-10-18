from helpers import facts_for, module_map, repo_index

from crgraph.build import build_graph
from crgraph.delta import compute_delta
from crgraph.model import Status, file_id, module_id, sym_id

ROOTS = {":core": "core", ":app": "app"}

REPO = """
    package com.example.core
    class Repo {
        fun load(id: Int): Int = id
        fun save(id: Int) {}
    }
"""
SCREEN = """
    package com.example.app
    import com.example.core.Repo
    class Screen {
        fun show() { Repo().load(1) }
    }
"""


def graph(files):
    facts = facts_for(files)
    mm = module_map(ROOTS)
    return build_graph(facts, mm, repo_index(facts, mm))


def delta(before_files, after_files, renames=None, prune=frozenset()):
    return compute_delta(graph(before_files), graph(after_files), renames or {}, prune)


def status(d, node_id):
    return d.nodes[node_id].status


def test_signature_change_and_propagation():
    after_repo = REPO.replace("fun load(id: Int)", "fun load(id: Long)")
    d = delta({"core/Repo.kt": REPO, "app/Screen.kt": SCREEN}, {"core/Repo.kt": after_repo, "app/Screen.kt": SCREEN})
    assert status(d, sym_id("core/Repo.kt", "Repo.load", 1)) == Status.MODIFIED
    assert status(d, sym_id("core/Repo.kt", "Repo.save", 1)) == Status.UNCHANGED
    assert status(d, sym_id("core/Repo.kt", "Repo", 0)) == Status.MODIFIED
    assert status(d, file_id("core/Repo.kt")) == Status.MODIFIED
    assert status(d, module_id(":core")) == Status.MODIFIED
    assert status(d, sym_id("app/Screen.kt", "Screen.show", 0)) == Status.IMPACTED
    assert status(d, file_id("app/Screen.kt")) == Status.IMPACTED
    assert status(d, module_id(":app")) == Status.IMPACTED


def test_formatting_only_change_is_unchanged():
    reformatted = REPO.replace("fun load(id: Int): Int = id", "fun load( id: Int ): Int =\n            id // same")
    d = delta({"core/Repo.kt": REPO}, {"core/Repo.kt": reformatted})
    assert {n.status for n in d.nodes.values()} == {Status.UNCHANGED}


def test_added_and_removed_symbols():
    d = delta({"core/Repo.kt": REPO}, {"core/Repo.kt": REPO.replace("fun save(id: Int) {}", "fun drop() {}")})
    assert status(d, sym_id("core/Repo.kt", "Repo.save", 1)) == Status.REMOVED
    assert status(d, sym_id("core/Repo.kt", "Repo.drop", 0)) == Status.ADDED
    assert d.nodes[sym_id("core/Repo.kt", "Repo.save", 1)].parent == sym_id("core/Repo.kt", "Repo", 0)


def test_renamed_file_without_edits_is_moved():
    d = delta({"core/Repo.kt": REPO}, {"core/data/Repo.kt": REPO}, {"core/Repo.kt": "core/data/Repo.kt"})
    f = d.nodes[file_id("core/data/Repo.kt")]
    assert f.status == Status.MOVED and f.from_path == "core/Repo.kt"
    assert status(d, sym_id("core/data/Repo.kt", "Repo.load", 1)) == Status.MOVED
    assert file_id("core/Repo.kt") not in d.nodes


def test_renamed_file_with_edits_is_modified_with_from_path():
    d = delta({"core/Repo.kt": REPO}, {"core/data/Repo.kt": REPO.replace("= id", "= id + 1")},
              {"core/Repo.kt": "core/data/Repo.kt"})
    f = d.nodes[file_id("core/data/Repo.kt")]
    assert f.status == Status.MODIFIED and f.from_path == "core/Repo.kt"
    assert status(d, sym_id("core/data/Repo.kt", "Repo.load", 1)) == Status.MODIFIED
    assert status(d, sym_id("core/data/Repo.kt", "Repo.save", 1)) == Status.MOVED


def test_symbol_moved_between_files_is_one_moved_node():
    before = {"core/Repo.kt": REPO, "core/Other.kt": "package com.example.core\nfun other() = 1\n"}
    after = {"core/Repo.kt": "package com.example.core\n", "core/Other.kt": "package com.example.core\nfun other() = 1\n" + REPO.replace("package com.example.core", "")}
    d = delta(before, after)
    moved = d.nodes[sym_id("core/Other.kt", "Repo", 0)]
    assert moved.status == Status.MOVED and moved.from_path == "core/Repo.kt"
    assert status(d, sym_id("core/Other.kt", "Repo.load", 1)) == Status.MOVED
    assert sym_id("core/Repo.kt", "Repo", 0) not in d.nodes


def test_edge_statuses():
    after_screen = SCREEN.replace("Repo().load(1)", "Repo().save(1)")
    d = delta({"core/Repo.kt": REPO, "app/Screen.kt": SCREEN}, {"core/Repo.kt": REPO, "app/Screen.kt": after_screen})
    by = {(e.src, e.dst, e.kind): e.status for e in d.edges}
    show = sym_id("app/Screen.kt", "Screen.show", 0)
    assert by[(show, sym_id("core/Repo.kt", "Repo.load", 1), "calls")] == Status.REMOVED
    assert by[(show, sym_id("core/Repo.kt", "Repo.save", 1), "calls")] == Status.ADDED
    assert by[(file_id("app/Screen.kt"), file_id("core/Repo.kt"), "imports")] == Status.UNCHANGED


def test_prune_drops_unchanged_expansion_files_only():
    after_repo = REPO.replace("fun load(id: Int)", "fun load(id: Long)")
    files_b = {"core/Repo.kt": REPO, "app/Screen.kt": SCREEN, "app/Unrelated.kt": "package com.example.app\nfun x() = 1\n"}
    files_a = {**files_b, "core/Repo.kt": after_repo}
    d = delta(files_b, files_a, prune={"app/Screen.kt", "app/Unrelated.kt"})
    assert file_id("app/Unrelated.kt") not in d.nodes
    assert sym_id("app/Unrelated.kt", "x", 0) not in d.nodes
    assert status(d, file_id("app/Screen.kt")) == Status.IMPACTED


def test_importing_a_modified_file_is_not_impact_by_itself():
    after_repo = REPO.replace("fun save(id: Int) {}", "fun save(id: Int) { println(id) }")
    d = delta({"core/Repo.kt": REPO, "app/Screen.kt": SCREEN}, {"core/Repo.kt": after_repo, "app/Screen.kt": SCREEN})
    assert status(d, file_id("core/Repo.kt")) == Status.MODIFIED
    assert status(d, sym_id("core/Repo.kt", "Repo", 0)) == Status.MODIFIED  # via its changed member
    assert status(d, file_id("app/Screen.kt")) == Status.UNCHANGED  # only uses Repo() and load()


def test_importing_a_removed_file_is_impact():
    d = delta({"core/Repo.kt": REPO, "app/Screen.kt": SCREEN}, {"app/Screen.kt": SCREEN})
    assert status(d, file_id("core/Repo.kt")) == Status.REMOVED
    assert status(d, file_id("app/Screen.kt")) == Status.IMPACTED


def test_qualname_of_handles_parentheses_in_names():
    from crgraph.model import qualname_of

    assert qualname_of("sym:a/T.kt#T.`loads (1) item`(0)") == "T.`loads (1) item`"
    assert qualname_of("file:a/T.kt") == "file:a/T.kt"


def test_member_removed_from_a_moved_class_stays_inside_it():
    before = {"core/Repo.kt": REPO, "core/Other.kt": "package com.example.core\nfun other() = 1\n"}
    moved_without_save = REPO.replace("package com.example.core", "").replace("        fun save(id: Int) {}\n", "")
    after = {"core/Repo.kt": "package com.example.core\n", "core/Other.kt": "package com.example.core\nfun other() = 1\n" + moved_without_save}
    d = delta(before, after)
    save = d.nodes[sym_id("core/Repo.kt", "Repo.save", 1)]
    assert save.status == Status.REMOVED
    assert save.parent == sym_id("core/Other.kt", "Repo", 0)  # the moved class, not its deleted old id
    assert all(n.parent is None or n.parent in d.nodes for n in d.nodes.values())
