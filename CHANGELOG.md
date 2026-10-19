# Changelog

## 0.4.1

- Report: the closed legend no longer covers the hint line when it wraps.
- `--explain`: the review never quotes JSON field names.

## 0.4.0

- `--explain` is now an architecture review: a verdict (Good / Acceptable / Needs work) with the reason, then
  architecture, coupling & cohesion, SOLID, DRY, testability, API compatibility, risks and suggestions, each
  based on named facts. The model also gets sizes, complexity, test flags, the overview and a duplication hint.
- Report: a ✦ verdict button in the header (Good / Acceptable / Needs work, reason on hover) opens the review in
  the story view. `summary.md` shows it as a table; `delta.json` has a parsed `review`.
- Report: a long range no longer squeezes the header; it is cut with "…" (full text on hover) and the counts get
  their own row.
- `--explain` through your own LLM proxy: a personal config, `~/.config/crgraph/config.yml`, can set the model,
  `base_url` and a token: inline (file must be `chmod 600`), from an environment variable or from a command. `plain: true` sends only standard request fields.
- A repository's `.crgraph.yml` rejects unknown `explain` keys and can never set the proxy or token.

## 0.3.0

- Report: a dark/light theme button; the choice is remembered in the browser.
- Report: dragging pans the graph also when it starts on a box; Shift+drag or middle-drag moves a box.

## 0.2.0

- Report: a *Tests* toggle hides test modules, files and symbols in the graph. `delta.json` nodes have a
  `test` flag.

## 0.1.0

First public release.

- Architecture-level report for any git range: modules ⊃ files ⊃ classes and functions, with before, merged and
  after views.
- Kotlin, Swift, TypeScript/JavaScript and Python. Modules come from Gradle, SwiftPM, npm and Python project files.
- Findings for new cycles, rule violations, API changes, module and external dependencies, coupling,
  complexity, test gaps, contract files, blast radius, splittable changes, dead code, hotspots, owners and
  changes to `.crgraph.yml`.
- Overview with verdict, review size, per-module table and "Start here".
- `report.html` (interactive, self-contained), `summary.md` (Mermaid and tables for MR comments) and
  `delta.json`.
- Opens the report straight away by default; `--generate` writes files for CI.
