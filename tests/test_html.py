import json
import re
from pathlib import Path

import pytest
from helpers import gradle_project, run_range
from test_render import python_with_tests

from crgraph.render.html import to_html
from crgraph.render.jsonout import to_document

REPO = "package com.example.core\nclass Repo { fun load(id: Int): Int = id }\n"


def document(repo):
    base = repo.commit({**gradle_project({":core": [], ":app": [":core"]}), "core/src/Repo.kt": REPO,
                        "app/src/Screen.kt": "package com.example.app\nimport com.example.core.Repo\nclass Screen { fun show() = Repo().load(1) }\n"})
    head = repo.commit({**gradle_project({":core": [":app"], ":app": [":core"]}),
                        "core/src/Repo.kt": REPO.replace("id: Int", "id: Long") + "// </script><b>x</b>\n"})
    return to_document(run_range(repo, base, head))


def test_html_is_self_contained_and_embeds_data(repo):
    doc = document(repo)
    page = to_html(doc)
    for name in ("TITLE", "CSS", "CYTOSCAPE", "CYTOSCAPE_DAGRE", "APP", "DATA"):
        assert "{{" + name + "}}" not in page
    assert "<script src=" not in page and "<link" not in page  # no network
    data = re.search(r'<script type="application/json" id="crgraph-data">(.*?)</script>', page, re.S).group(1)
    assert json.loads(data)["range"]["label"] == doc["range"]["label"]
    assert len(page) < 1_500_000


def test_html_escapes_script_end_in_data():
    doc = {"range": {"label": "a</script><script>alert(1)</script>"}, "nodes": [], "edges": [], "findings": [],
           "stats": {}, "warnings": [], "owners": {}, "explain": None, "ui": {"depth": "module"}}
    page = to_html(doc)
    assert "</script><script>alert(1)" not in page



def test_html_data_block_has_no_markup_characters():
    label = "a<!--<script>b</script>&c"
    doc = {"range": {"label": label}, "nodes": [], "edges": [], "findings": [],
           "stats": {}, "warnings": [], "owners": {}, "explain": None, "ui": {"depth": "module"}}
    page = to_html(doc)
    data = page.split('id="crgraph-data">', 1)[1].split("</script>", 1)[0]
    assert not set("<>&") & set(data)
    assert json.loads(data)["range"]["label"] == label

def test_report_renders_in_a_browser(repo, tmp_path):
    sync_api = pytest.importorskip("playwright.sync_api")
    path = tmp_path / "report.html"
    path.write_text(to_html(document(repo)))
    with sync_api.sync_playwright() as p:
        try:
            browser = p.chromium.launch(channel="chrome")
        except Exception:
            try:
                browser = p.chromium.launch()
            except Exception as exc:  # no browser installed
                pytest.skip(f"no chromium available: {exc}")
        page = browser.new_page()
        errors = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
        page.goto(path.as_uri())
        page.wait_for_function("window.__crgraph && window.__crgraph.cy().nodes().length > 0")
        assert errors == []
        assert page.locator("#verdict").inner_text().startswith("Size S · Mostly changes to existing code in :core")
        assert page.locator("#tests-filter").is_hidden()  # nothing to filter
        assert page.locator("#legend[open]").count() == 0  # collapsed: it would cover the graph
        page.locator("#legend summary").click()
        legend = page.locator("#legend").inner_text()
        for words in ("depends on", "imports", "calls", "references", "inherits", "added", "removed", "by name only"):
            assert words in legend
        page.evaluate("window.__crgraph.cy().edges().first().emit('tap')")
        details = page.locator("#details").inner_text()
        assert details.startswith("CONNECTION") or details.startswith("connection")
        assert "depends on" in details
        assert page.locator(".finding").count() >= 1
        page.locator(".finding").first.click()
        assert page.evaluate("window.__crgraph.state.highlight.size") > 0
        page.locator("[data-depth=file]").click()
        assert page.evaluate("window.__crgraph.cy().nodes('.file').length") > 0
        page.locator("#btn-story").click()
        assert page.locator("#story h2").count() >= 2
        overview = page.locator("#story .overview")
        assert overview.locator("table tr").count() >= 2 and "Start here" in overview.inner_text()
        assert overview.locator("li code").first.inner_text() == "core/src/Repo.kt"
        assert errors == []
        browser.close()


def test_story_links_open_the_graph_and_are_readable_in_dark_mode(repo, tmp_path):
    sync_api = pytest.importorskip("playwright.sync_api")
    path = tmp_path / "report.html"
    path.write_text(to_html(document(repo)))
    with sync_api.sync_playwright() as p:
        try:
            browser = p.chromium.launch(channel="chrome")
        except Exception:
            try:
                browser = p.chromium.launch()
            except Exception as exc:  # no browser installed
                pytest.skip(f"no chromium available: {exc}")
        page = browser.new_page(color_scheme="dark")
        page.goto(path.as_uri())
        page.wait_for_function("window.__crgraph && window.__crgraph.cy().nodes().length > 0")
        page.locator("#btn-story").click()
        link = page.locator("#story .overview li a").first
        fg, bg = page.evaluate("""el => {
          const rgb = c => c.match(/\\d+/g).slice(0, 3).map(Number);
          const lum = c => { const v = rgb(c).map(x => { x /= 255; return x <= 0.03928 ? x / 12.92 : ((x + 0.055) / 1.055) ** 2.4; });
                             return 0.2126 * v[0] + 0.7152 * v[1] + 0.0722 * v[2]; };
          return [lum(getComputedStyle(el).color), lum(getComputedStyle(document.body).backgroundColor)]; }""", link.element_handle())
        assert (max(fg, bg) + 0.05) / (min(fg, bg) + 0.05) >= 4.5  # WCAG AA contrast for text
        link.click()
        assert page.locator("#story").is_hidden()
        assert page.locator("#btn-story").inner_text() == "Story view"
        assert page.evaluate("window.__crgraph.state.selected") == "file:core/src/Repo.kt"
        assert page.evaluate("window.__crgraph.cy().getElementById('file:core/src/Repo.kt').nonempty()")
        assert "core/src/Repo.kt" in page.locator("#details").inner_text()
        page.wait_for_timeout(400)  # the jump is animated
        assert page.evaluate("window.__crgraph.cy().zoom()") <= 1.5  # jumping to one box must not zoom in to 3x
        page.evaluate("window.__crgraph.cy().getElementById('module::core').emit('tap')")
        pairs = page.evaluate("""[...document.querySelectorAll('#details dl.meta > *')].map(e => e.tagName)""")
        assert pairs[:2] == ["DT", "DD"] and pairs == ["DT", "DD"] * (len(pairs) // 2)  # labels line up with values
        assert page.locator("#details dl.meta dt").first.inner_text() == "path"
        browser.close()

def test_tests_toggle_hides_test_code_and_comes_back_for_a_search(repo, tmp_path):
    sync_api = pytest.importorskip("playwright.sync_api")
    path = tmp_path / "report.html"
    path.write_text(to_html(to_document(python_with_tests(repo), depth="symbol")))
    with sync_api.sync_playwright() as p:
        try:
            browser = p.chromium.launch(channel="chrome")
        except Exception:
            try:
                browser = p.chromium.launch()
            except Exception as exc:  # no browser installed
                pytest.skip(f"no chromium available: {exc}")
        page = browser.new_page()
        page.goto(path.as_uri())
        page.wait_for_function("window.__crgraph && window.__crgraph.cy().nodes().length > 0")
        ids = "window.__crgraph.cy().nodes().map(n => n.id()).sort()"
        assert "sym:tests/test_calc.py#test_add(0)" in page.evaluate(ids)
        page.locator("#tests").uncheck()
        assert page.evaluate(ids) == ["file:pkg/calc.py", "module:pkg", "sym:pkg/calc.py#add(2)"]
        page.locator("#search").fill("test_add")
        page.wait_for_function("document.getElementById('tests').checked")  # a hit inside the tests shows them again
        assert "sym:tests/test_calc.py#test_add(0)" in page.evaluate(ids)
        browser.close()


def test_symbol_depth_on_a_large_change_shows_the_most_changed_symbols(repo, tmp_path):
    sync_api = pytest.importorskip("playwright.sync_api")
    funcs = lambda v: "".join(f"def f{i}():\n    return {v}\n\n" for i in range(120))  # noqa: E731
    base = repo.commit({f"pkg/m{i}.py": funcs(1) for i in range(3)})
    head = repo.commit({f"pkg/m{i}.py": funcs(2) for i in range(3)})
    path = tmp_path / "report.html"
    path.write_text(to_html(to_document(run_range(repo, base, head))))
    with sync_api.sync_playwright() as p:
        try:
            browser = p.chromium.launch(channel="chrome")
        except Exception:
            try:
                browser = p.chromium.launch()
            except Exception as exc:  # no browser installed
                pytest.skip(f"no chromium available: {exc}")
        page = browser.new_page()
        page.goto(path.as_uri())
        page.wait_for_function("window.__crgraph && window.__crgraph.cy().nodes().length > 0")
        page.locator("[data-depth=symbol]").click()
        kinds = page.evaluate("window.__crgraph.cy().nodes().map(n => n.classes().join(' '))")
        assert sum("function" in k for k in kinds) == 240  # two files opened, not collapsed back to modules
        assert len(kinds) <= 300
        assert "1 more box(es) can be opened with a double-click" in page.locator("#hint").inner_text()
        page.locator("[data-depth=module]").click()
        assert page.locator("#hint").inner_text().startswith("Click a node or a line for details")
        browser.close()


def test_module_view_groups_areas_and_hides_implied_dependencies(repo, tmp_path):
    sync_api = pytest.importorskip("playwright.sync_api")
    mods = {":android:a": [":android:b", ":android:c"], ":android:b": [":android:c"], ":android:c": [], ":android:d": [],
            ":shared:e": [], ":shared:f": [], ":shared:g": [], ":shared:h": []}
    src = lambda m, v: {f"{m.strip(':').replace(':', '/')}/src/X{m[-1]}.kt": f"package p{m[-1]}\nfun f() = {v}\n" for m in mods}  # noqa: E731
    base = repo.commit({**gradle_project(mods), **{k: v for m in mods for k, v in src(m, 1).items()}})
    head = repo.commit({k: v for m in mods for k, v in src(m, 2).items()})
    path = tmp_path / "report.html"
    path.write_text(to_html(to_document(run_range(repo, base, head))))
    with sync_api.sync_playwright() as p:
        try:
            browser = p.chromium.launch(channel="chrome")
        except Exception:
            try:
                browser = p.chromium.launch()
            except Exception as exc:  # no browser installed
                pytest.skip(f"no chromium available: {exc}")
        page = browser.new_page()
        page.goto(path.as_uri())
        page.wait_for_function("window.__crgraph && window.__crgraph.cy().nodes().length > 0")
        areas = page.evaluate("window.__crgraph.cy().nodes('.area').map(n => n.data('label')).sort()")
        assert areas == ["android", "shared"]
        assert page.evaluate("window.__crgraph.cy().getElementById('module::android:a').parent().id()") == "area:android"
        lines = page.evaluate("window.__crgraph.cy().edges().map(e => e.source().id() + '>' + e.target().id())")
        assert "module::android:a>module::android:b" in lines and "module::android:b>module::android:c" in lines
        assert "module::android:a>module::android:c" not in lines  # implied by a > b > c
        browser.close()


def test_nested_symbol_view_never_piles_boxes_up(tmp_path):
    """Anonymised graph (names masked) on which dagre's compound layout throws and left every box at one spot."""
    sync_api = pytest.importorskip("playwright.sync_api")
    data = json.loads((Path(__file__).parent / "data" / "nested_layout_crash.json").read_text())
    doc = {"range": {"label": "a..b"}, "stats": {"modules": {}, "files": {}, "symbols": {}, "findings": {}},
           "findings": [], "warnings": [], "owners": {}, "explain": None, "ui": {"depth": "module"}, **data}
    path = tmp_path / "report.html"
    path.write_text(to_html(doc))
    with sync_api.sync_playwright() as p:
        try:
            browser = p.chromium.launch(channel="chrome")
        except Exception:
            try:
                browser = p.chromium.launch()
            except Exception as exc:  # no browser installed
                pytest.skip(f"no chromium available: {exc}")
        page = browser.new_page()
        errors = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.goto(path.as_uri())
        page.wait_for_function("window.__crgraph && window.__crgraph.cy().nodes().length > 0")
        for depth in ("symbol", "file", "module", "symbol"):
            page.locator(f"[data-depth={depth}]").click()
            overlaps = page.evaluate("""(() => { const ns = window.__crgraph.cy().nodes(':childless').toArray(); let o = 0;
              for (let i = 0; i < ns.length; i++) for (let j = i + 1; j < ns.length; j++) {
                const a = ns[i].boundingBox(), b = ns[j].boundingBox();
                if (a.x1 < b.x2 && b.x1 < a.x2 && a.y1 < b.y2 && b.y1 < a.y2) o++; }
              return o; })()""")
            assert (depth, overlaps) == (depth, 0)
        assert errors == []
        browser.close()


def test_existing_dependency_with_new_usage_is_a_changed_line_not_a_new_one(repo, tmp_path):
    sync_api = pytest.importorskip("playwright.sync_api")
    core = "package com.example.core\nclass Repo { fun load(id: Int): Int = id }\nclass Cache { fun get(): Int = 1 }\n"
    screen = "package com.example.app\nimport com.example.core.Repo\nclass Screen { fun show() = Repo().load(1) }\n"
    base = repo.commit({**gradle_project({":core": [], ":app": [":core"]}), "core/src/Core.kt": core, "app/src/Screen.kt": screen})
    head = repo.commit({"core/src/Core.kt": core.replace("= 1", "= 2"),  # core is part of the change too
                        "app/src/Screen.kt": screen.replace("import com.example.core.Repo\n", "import com.example.core.Repo\nimport com.example.core.Cache\n")
                        .replace("Repo().load(1)", "Repo().load(Cache().get())")})
    path = tmp_path / "report.html"
    path.write_text(to_html(to_document(run_range(repo, base, head))))
    with sync_api.sync_playwright() as p:
        try:
            browser = p.chromium.launch(channel="chrome")
        except Exception:
            try:
                browser = p.chromium.launch()
            except Exception as exc:  # no browser installed
                pytest.skip(f"no chromium available: {exc}")
        page = browser.new_page()
        page.goto(path.as_uri())
        page.wait_for_function("window.__crgraph && window.__crgraph.cy().nodes().length > 0")
        line = "window.__crgraph.cy().edges().filter(e => e.source().id() === 'module::app' && e.target().id() === 'module::core')"
        statuses = {}
        for mode in ("before", "merged", "after"):
            page.locator(f"[data-mode={mode}]").click()
            statuses[mode] = page.evaluate(line + ".map(e => ['added', 'removed', 'modified', 'unchanged'].find(s => e.hasClass(s)))")
        assert statuses == {"before": ["unchanged"], "merged": ["modified"], "after": ["modified"]}
        assert "existed before" in page.locator("#legend").inner_html()
        browser.close()
