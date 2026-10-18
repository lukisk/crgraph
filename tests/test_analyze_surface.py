from helpers import findings, gradle_project, run_range

from crgraph.analyze.history import owners_for, parse_codeowners


def test_test_gap(repo):
    base = repo.commit({**gradle_project({":core": [], ":app": []}),
                        "core/src/main/kotlin/Repo.kt": "package c\nclass Repo\n",
                        "core/src/test/kotlin/RepoTest.kt": "package c\nclass RepoTest\n",
                        "core/src/main/kotlin/Cache.kt": "package c\nclass Cache\n",
                        "app/src/main/kotlin/Screen.kt": "package a\nclass Screen\n"})
    head = repo.commit({"core/src/main/kotlin/Repo.kt": "package c\nclass Repo { fun x() = 1 }\n",
                        "core/src/main/kotlin/Cache.kt": "package c\nclass Cache { fun y() = 1 }\n",
                        "core/src/test/kotlin/RepoTest.kt": "package c\nclass RepoTest { fun t() = 1 }\n",
                        "app/src/main/kotlin/Screen.kt": "package a\nclass Screen { fun z() = 1 }\n"})
    titles = sorted(f.title for f in findings(run_range(repo, base, head), "test-gap"))
    assert titles == ["Changed types without a matching test change in :core: Cache",
                      "No test changes in :app (1 changed source file(s))"]


def test_external_dependency_added(repo):
    base = repo.commit({"web/package.json": '{"name": "web", "dependencies": {"react": "18"}}'})
    head = repo.commit({"web/package.json": '{"name": "web", "dependencies": {"react": "18", "left-pad": "1"}}'})
    assert [f.title for f in findings(run_range(repo, base, head), "external-dep-added")] == [
        "New external dependencies in web: npm:left-pad"]


def test_contract_surface(repo):
    base = repo.commit({
        "app/src/main/AndroidManifest.xml": '<manifest><uses-permission android:name="android.permission.INTERNET"/></manifest>',
        "svc/app.py": "import os\nTOKEN = os.environ['TOKEN']\n",
        "svc/routes.ts": "router.get('/health', h)\n",
    })
    head = repo.commit({
        "app/src/main/AndroidManifest.xml": '<manifest><uses-permission android:name="android.permission.INTERNET"/>'
                                            '<uses-permission android:name="android.permission.POST_NOTIFICATIONS"/></manifest>',
        "svc/app.py": "import os\nTOKEN = os.environ['TOKEN']\nURL = os.getenv('API_URL')\n",
        "svc/routes.ts": "router.get('/health', h)\nrouter.post('/questions', q)\n",
    })
    found = {f.title: f.detail for f in findings(run_range(repo, base, head), "contract-surface")}
    assert found["Contract file changed: app/src/main/AndroidManifest.xml"] == \
        "added permissions: android.permission.POST_NOTIFICATIONS"
    assert found["Environment variables changed in svc/app.py"] == "added environment variables: API_URL"
    assert found["Http routes changed in svc/routes.ts"] == "added HTTP routes: /questions"


def test_codeowners_parsing_and_matching():
    rules = parse_codeowners("# comment\n[Section]\n* @all\n*.kt @android\n/ios/ @ios-team\ndocs/x.md @docs # inline\n")
    assert owners_for("a/b/C.kt", rules) == ["@android"]
    assert owners_for("ios/App/A.swift", rules) == ["@ios-team"]
    assert owners_for("lib/ios/A.swift", rules) == ["@all"]
    assert owners_for("docs/x.md", rules) == ["@docs"]
    assert owners_for("README.md", rules) == ["@all"]


def test_owners_finding_and_map(repo):
    base = repo.commit({"CODEOWNERS": "*.py @py\n/web/ @web\n", "a.py": "x = 1\n", "web/a.ts": "export const a = 1\n"})
    head = repo.commit({"a.py": "x = 2\n", "web/a.ts": "export const a = 2\n"})
    result = run_range(repo, base, head)
    assert [f.title for f in findings(result, "owners")] == ["Touches 2 owner group(s): @py, @web"]
    assert result.owners == {"a.py": ["@py"], "web/a.ts": ["@web"]}


def test_hotspot(repo):
    branchy = "def f(x):\n" + "".join(f"    if x == {i}:\n        return {i}\n" for i in range(22)) + "    return x\n"
    repo.commit({"hot.py": branchy, "cold.py": "y = 1\n"})
    for i in range(4):
        repo.commit({"hot.py": branchy + f"# {i}\n"})
    base = repo.git("rev-parse", "HEAD")
    head = repo.commit({"hot.py": branchy.replace("return x", "return -x")})
    hot = findings(run_range(repo, base, head), "hotspot")
    assert [f.title for f in hot] == ["1 changed file(s) are hotspots (frequently changed and complex)"]
    assert hot[0].detail.startswith("hot.py: 6 commits")
