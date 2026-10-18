# Contributing

Issues and pull requests are welcome. For larger changes, open an issue first so we can agree on the approach.

## Setup

```sh
git clone https://github.com/lukisk/crgraph && cd crgraph
uv venv && uv pip install -e '.[dev]'    # or: python -m venv .venv && .venv/bin/pip install -e '.[dev]'
.venv/bin/pytest                         # browser tests skip unless Chromium is installed:
.venv/bin/playwright install chromium    #   (optional) enables them
.venv/bin/ruff check
```

`uv tool install -e .` gives you a global `crgraph` command that runs your checkout.

## Layout

| Path | What it does |
|---|---|
| `src/crgraph/cli.py` | arguments, config loading, output files, exit codes |
| `src/crgraph/gitio.py` | all git access: ranges, changed files, blobs, grep, history |
| `src/crgraph/modules/` | module detection from build files (Gradle, SwiftPM, npm, Python) |
| `src/crgraph/lang/` | tree-sitter language plugins: symbols, signatures, imports, complexity |
| `src/crgraph/resolve.py`, `build.py` | imports and names to dependency edges, the before/after graphs |
| `src/crgraph/delta.py`, `expand.py` | the change between the graphs, plus one hop of impacted code |
| `src/crgraph/analyze/` | the findings; each module exports a `CHECKS` list |
| `src/crgraph/overview.py` | verdict, review size, module table, "Start here" |
| `src/crgraph/render/` | `delta.json`, `summary.md` and the self-contained `report.html` (assets in `render/assets/`) |
| `tests/` | pytest; `conftest.py` has a `repo` fixture that builds throwaway git repositories |

## Adding a language

1. Add a plugin in `src/crgraph/lang/` modelled on an existing one (`python.py` is the smallest), and register
   it in `PLUGINS` and `GRAMMARS` in `lang/__init__.py`.
2. If the ecosystem has its own module or package files, add a detector in `src/crgraph/modules/`.
3. Add tests in `tests/lang/` for symbols, signatures, visibility and imports.

## Adding a finding

Write a function `check(ctx: AnalysisContext) -> list[Finding]` in the fitting `analyze/` module, add it to
that module's `CHECKS`, add the rule to the table in `README.md`, and give it a readable name in `RULE_NAME` in
`render/assets/report.js`.

## Ground rules

- crgraph never shows source code. Outputs carry names, paths, signatures and metrics only.
- Everything runs locally. `--explain` is the only feature that talks to the network, and only when asked.
- Treat repository content as untrusted: names end up in HTML and Markdown and must stay escaped.

## Screenshots

`docs/demo_repo.py` builds a small Kotlin demo repository with a reviewable change, and
`docs/screenshots.py` regenerates the images in `docs/images/` from its report (it needs Google Chrome):

```sh
python docs/demo_repo.py /tmp/shop
(cd /tmp/shop && crgraph main...feature/quick-buy -o /tmp/shop-out --fail-on never)
.venv/bin/python docs/screenshots.py /tmp/shop-out/report.html docs/images
```

## Project site

`site/index.html` is the landing page at https://lukisk.github.io/crgraph/. The Pages workflow publishes it on
every push to `main` together with a live demo report built from `docs/demo_repo.py`.

## Releases

1. Bump `__version__` in `src/crgraph/__init__.py` and add a section to `CHANGELOG.md`.
2. Commit, then `git tag v0.2.0 && git push origin main v0.2.0`.

The Release workflow checks that the tag matches the version, builds the wheel and sdist, and attaches them to
a GitHub release.
