# Changelog

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
