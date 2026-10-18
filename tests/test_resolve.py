from crgraph.model import FileFacts
from crgraph.modules import ModuleInfo, ModuleMap
from crgraph.resolve import RepoIndex


def index(files, modules=(), package_lines=()):
    mm = ModuleMap()
    for info in modules:
        mm.add(info)
    calls = []

    def lines():
        calls.append(1)
        return list(package_lines)

    return RepoIndex(files, mm, lines), calls


def test_kotlin_imports_resolve_through_package_index():
    files = ["core/src/com/example/core/Repo.kt", "core/src/com/example/core/Util.kt", "core/src/com/example/core/Outer.kt"]
    idx, calls = index(files, [ModuleInfo(":core", "core", "gradle")],
                       [(f, "package com.example.core") for f in files])
    f = FileFacts("app/src/A.kt", "kotlin", imports=["com.example.core.Repo", "com.example.core.*", "com.example.core.topLevelFn",
                                                     "com.example.core.Outer.Inner", "kotlinx.coroutines.flow.Flow"])
    targets = {t.raw: t for t in idx.resolve(f)}
    assert targets["com.example.core.Repo"].files == {"core/src/com/example/core/Repo.kt"}
    assert targets["com.example.core.Repo"].modules == {":core"}
    assert targets["com.example.core.*"].files == set() and targets["com.example.core.*"].modules == {":core"}
    assert targets["com.example.core.topLevelFn"].files == set()  # top-level function: module known, file not
    assert targets["com.example.core.Outer.Inner"].files == {"core/src/com/example/core/Outer.kt"}
    assert "kotlinx.coroutines.flow.Flow" not in targets
    assert len(calls) == 1  # package index built once, lazily


def test_package_index_not_built_without_kotlin():
    idx, calls = index(["a.py"])
    idx.resolve(FileFacts("a.py", "python", imports=["os"]))
    assert calls == []


def test_swift_imports_map_to_modules():
    idx, _ = index([], [ModuleInfo("ShopCore", "ios/ShopCore", "swift")])
    targets = idx.resolve(FileFacts("ios/App/A.swift", "swift", imports=["Foundation", "ShopCore"]))
    assert [(t.raw, t.modules) for t in targets] == [("ShopCore", frozenset({"ShopCore"}))]


def test_typescript_relative_and_workspace_imports():
    files = ["web/src/repo.ts", "web/src/util/index.tsx"]
    idx, _ = index(files, [ModuleInfo("@acme/core", "packages/core", "npm")])
    f = FileFacts("web/src/app/A.tsx", "typescript", imports=["../repo", "../util", "@acme/core/x", "react"])
    targets = {t.raw: t for t in idx.resolve(f)}
    assert targets["../repo"].files == {"web/src/repo.ts"}
    assert targets["../util"].files == {"web/src/util/index.tsx"}
    assert targets["@acme/core/x"].modules == {"@acme/core"}
    assert "react" not in targets


def test_python_relative_and_absolute_imports():
    files = ["clients/python/src/quiz/api.py", "clients/python/src/quiz/models/__init__.py", "tools/x.py"]
    idx, _ = index(files, [ModuleInfo("quiz-client", "clients/python", "python")])
    f = FileFacts("clients/python/src/quiz/cli.py", "python", imports=[".api", ".models", "quiz.api", "tools.x", "os"])
    targets = {t.raw: t.files for t in idx.resolve(f)}
    assert targets == {
        ".api": {"clients/python/src/quiz/api.py"},
        ".models": {"clients/python/src/quiz/models/__init__.py"},
        "quiz.api": {"clients/python/src/quiz/api.py"},
        "tools.x": {"tools/x.py"},
    }


def test_main_code_never_resolves_into_test_sources():
    files = ["core/src/main/com/example/core/Repo.kt", "core/src/test/com/example/core/Repo.kt"]
    mm = ModuleMap()
    mm.add(ModuleInfo(":core", "core", "gradle"))
    idx = RepoIndex(files, mm, lambda: [(f, "package com.example.core") for f in files], ["**/test/**"])
    main = idx.resolve(FileFacts("app/src/main/A.kt", "kotlin", imports=["com.example.core.Repo"]))
    test = idx.resolve(FileFacts("app/src/test/ATest.kt", "kotlin", imports=["com.example.core.Repo"]))
    assert main[0].files == {"core/src/main/com/example/core/Repo.kt"}
    assert test[0].files == set(files)
