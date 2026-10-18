from helpers import facts_for, module_map, repo_index

from crgraph.build import build_graph, parse_files
from crgraph.gitio import Repo
from crgraph.model import Edge, file_id, module_id, sym_id

FILES = {
    "core/src/com/example/core/Repo.kt": """
        package com.example.core
        class Repo {
            fun load(id: Int): Int = id
        }
        fun util() = 1
    """,
    "app/src/com/example/app/Screen.kt": """
        package com.example.app
        import com.example.core.Repo
        class Screen {
            fun show() { Repo().load(1); util() }
        }
    """,
    "app/src/com/example/app/Other.kt": """
        package com.example.app
        class Other {
            fun run() { load(2) }
        }
    """,
}


def graph():
    facts = facts_for(FILES)
    mm = module_map({":core": "core", ":app": "app"})
    return build_graph(facts, mm, repo_index(facts, mm))


def test_hierarchy():
    g = graph()
    load = g.nodes[sym_id("core/src/com/example/core/Repo.kt", "Repo.load", 1)]
    assert load.parent == sym_id("core/src/com/example/core/Repo.kt", "Repo", 0)
    assert g.nodes[load.parent].parent == file_id("core/src/com/example/core/Repo.kt")
    assert g.nodes[file_id("core/src/com/example/core/Repo.kt")].parent == module_id(":core")
    assert load.signature == "fun load(id: Int): Int" and load.module == ":core"


def test_imports_and_derived_module_dependency():
    g = graph()
    assert Edge(file_id("app/src/com/example/app/Screen.kt"), file_id("core/src/com/example/core/Repo.kt"), "imports") in g.edges
    assert Edge(module_id(":app"), module_id(":core"), "depends_on", derived=True) in g.edges


def test_declared_dependency_suppresses_derived_duplicate():
    facts = facts_for(FILES)
    mm = module_map({":core": "core", ":app": "app"}, deps=[(":app", ":core")])
    g = build_graph(facts, mm, repo_index(facts, mm))
    deps = [e for e in g.edges if e.kind == "depends_on"]
    assert deps == [Edge(module_id(":app"), module_id(":core"), "depends_on")]


def test_calls_resolve_and_members_need_owner_reference():
    g = graph()
    show = sym_id("app/src/com/example/app/Screen.kt", "Screen.show", 0)
    run = sym_id("app/src/com/example/app/Other.kt", "Other.run", 0)
    load = sym_id("core/src/com/example/core/Repo.kt", "Repo.load", 1)
    util = sym_id("core/src/com/example/core/Repo.kt", "util", 0)
    calls = {(e.src, e.dst) for e in g.edges if e.kind == "calls"}
    assert (show, load) in calls
    assert (show, util) in calls
    assert (run, load) not in calls  # Other.kt never mentions Repo
    assert all(e.confidence == "heuristic" for e in g.edges if e.kind == "calls")


def test_no_edges_to_own_ancestors():
    g = graph()
    for e in g.edges:
        if e.kind in ("calls", "references", "inherits"):
            node = g.nodes[e.src]
            parents = set()
            while node.parent:
                parents.add(node.parent)
                node = g.nodes[node.parent]
            assert e.dst not in parents


def test_parse_files_handles_other_and_missing_files(repo):
    sha = repo.commit({"a.py": "def f():\n    pass\n", "README.md": "# hi\n"})
    facts = parse_files(Repo(repo.root), sha, ["a.py", "README.md", "missing.py"])
    assert set(facts) == {"a.py", "README.md"}
    assert facts["README.md"].lang == "other" and facts["README.md"].file_hash
    assert [s.name for s in facts["a.py"].symbols] == ["f"]


def test_main_code_does_not_call_into_tests():
    files = {
        "core/src/main/com/example/core/Repo.kt": "package com.example.core\nfun helper() = 1\n",
        "core/src/test/com/example/core/Fakes.kt": "package com.example.core\nfun fakeRepo() = helper()\n",
        "core/src/main/com/example/core/Use.kt": "package com.example.core\nfun use() = fakeRepo()\n",
    }
    facts = facts_for(files)
    mm = module_map({":core": "core"})
    lines = [(p, "package com.example.core") for p in files]
    from crgraph.resolve import RepoIndex
    g = build_graph(facts, mm, RepoIndex(list(files), mm, lambda: lines, ["**/test/**"]))
    calls = {(e.src, e.dst) for e in g.edges if e.kind == "calls"}
    assert (sym_id("core/src/test/com/example/core/Fakes.kt", "fakeRepo", 0), sym_id("core/src/main/com/example/core/Repo.kt", "helper", 0)) in calls
    assert not any(dst.startswith("sym:core/src/test/") for _, dst in calls if not _.startswith("sym:core/src/test/"))


def test_type_names_never_resolve_to_extensions_and_private_stays_in_its_file():
    files = {
        "ios/A/Analytics.swift": "public struct Analytics {\n    public func log() {}\n}\n",
        "ios/A/Player.swift": ("private extension Analytics {\n    func trackOpen() {}\n}\n"
                               "extension Analytics {\n    func trackClose() {}\n}\n"
                               "private func helper() {}\nfunc play() { helper() }\n"),
        "ios/A/Screen.swift": ("func show(a: Analytics) {\n    a.log()\n    a.trackOpen()\n    a.trackClose()\n    helper()\n}\n"),
    }
    facts = facts_for(files)
    mm = module_map({"A": "ios/A"})
    g = build_graph(facts, mm, repo_index(facts, mm))
    targets = {e.dst for e in g.edges if e.src.startswith("sym:ios/A/Screen.swift")}
    assert sym_id("ios/A/Analytics.swift", "Analytics", 0) in targets  # the type itself
    assert sym_id("ios/A/Analytics.swift", "Analytics.log", 0) in targets
    assert sym_id("ios/A/Player.swift", "Analytics.trackClose", 0) in targets  # member of a non-private extension
    assert not any(t.startswith("sym:ios/A/Player.swift#Analytics(") for t in targets)  # never the extension itself
    assert sym_id("ios/A/Player.swift", "Analytics.trackOpen", 0) not in targets  # inside a private extension
    assert sym_id("ios/A/Player.swift", "helper", 0) not in targets  # private, other file
    assert Edge(sym_id("ios/A/Player.swift", "play", 0), sym_id("ios/A/Player.swift", "helper", 0), "calls",
                confidence="heuristic") in g.edges  # same file still resolves
