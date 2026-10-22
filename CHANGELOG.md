# Changelog

## Unreleased

- `summary.md` follows `--depth`: with `file` the Mermaid diagram shows changed files grouped by module, with
  `symbol` changed symbols grouped by file, and a table lists every changed file or symbol (status, moves,
  symbol counts, signatures before → after, test code marked). The default `module` output is unchanged.
- Progress labels drop control characters: the AI review label shows the model name, which a repository's
  `.crgraph.yml` can set.

## 0.5.1

- A progress line on the terminal while crgraph works: the current step, files parsed so far and the elapsed
  time, so a large range or a slow AI review no longer looks stuck. It is cleared before the summary and never
  written to pipes or CI logs; `-q`/`--quiet` turns it off.

## 0.5.0

- Go: functions, methods (grouped under their receiver type), structs, interfaces, constants and imports.
  Every package directory under a `go.mod` is a module named by its import path, so imports resolve to files and
  the module graph shows package dependencies. `go.mod` requirements are external dependencies; `*_test.go`
  files are tests.
- PHP: classes, interfaces, traits, enums, methods, properties, functions and `use` statements. Every
  `composer.json` is a module, and every directory under a PSR-4 autoload root is a module named by its
  namespace. Composer requirements are external dependencies; `*Test.php` files are tests.
- `test-gap`: tests kept in their own folder or module (`tests/`, `Tests\...`) count for the code they are named
  after, so Python and PHP changes with tests no longer report a missing test change.

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
