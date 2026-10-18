"""Runs every stage for one range."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .analyze import AnalysisContext, run_analyses
from .build import build_graph, parse_files
from .config import CONFIG_FILE, Config
from .delta import compute_delta
from .expand import changed_symbol_names, find_impacted_files
from .gitio import FileChange, RangeSpec, Repo
from .globs import match_any
from .graphalg import strongly_connected
from .model import SEVERITY_RANK, FileFacts, Finding, Graph, GraphDelta, Severity, file_id
from .modules import ModuleMap, detect_modules
from .overview import build_overview
from .resolve import KOTLIN_PACKAGE_GREP, RepoIndex

MAX_NEIGHBOURS_PER_IMPORT = 5


@dataclass
class RunResult:
    rng: RangeSpec
    changes: list[FileChange]
    before: Graph
    after: Graph
    delta: GraphDelta
    findings: list[Finding]
    mods_before: ModuleMap
    mods_after: ModuleMap
    facts_before: dict[str, FileFacts]
    facts_after: dict[str, FileFacts]
    warnings: list[str] = field(default_factory=list)
    owners: dict[str, list[str]] = field(default_factory=dict)
    overview: dict[str, Any] = field(default_factory=dict)


def _import_neighbours(facts: dict[str, FileFacts], index: RepoIndex, modules: ModuleMap) -> set[str]:
    """Same-module files imported by changed files (needed to see file-level cycles)."""
    out: set[str] = set()
    for path, f in facts.items():
        module = modules.module_of(path)
        for target in index.resolve(f):
            if len(target.files) <= MAX_NEIGHBOURS_PER_IMPORT:
                out |= {t for t in target.files if modules.module_of(t) == module}
    return out


def run(repo: Repo, rng: RangeSpec, config: Config, pathspecs: list[str] | None = None) -> RunResult:
    pathspecs = pathspecs or []
    warnings: list[str] = []
    all_changes = repo.changed_files(rng, pathspecs)
    changes = [c for c in all_changes if not match_any(config.ignore, c.path)]
    renames = {c.old_path: c.path for c in changes if c.status == "R" and c.old_path}

    files_before, files_after = repo.list_files(rng.base), repo.list_files(rng.head)
    # with a pathspec, "after" is the base plus only the selected changes (build files included)
    selected = {c.head_path for c in changes if c.head_path} if pathspecs else None
    if selected is not None:
        files_after = sorted((set(files_before) - {c.base_path for c in changes if c.base_path}) | selected)

    def read_after(paths: list[str]) -> dict[str, bytes | None]:
        if selected is None:
            return repo.read_many(rng.head, paths)
        out = repo.read_many(rng.base, [p for p in paths if p not in selected])
        out.update(repo.read_many(rng.head, [p for p in paths if p in selected]))
        return out

    mods_before = detect_modules(files_before, lambda paths: repo.read_many(rng.base, paths), config)
    mods_after = detect_modules(files_after, read_after, config)
    index_before = RepoIndex(files_before, mods_before,
                             lambda: repo.grep_lines(rng.base, KOTLIN_PACKAGE_GREP, ["*.kt", "*.kts"]),
                             config.test_patterns)
    index_after = RepoIndex(files_after, mods_after,
                            lambda: repo.grep_lines(rng.head, KOTLIN_PACKAGE_GREP, ["*.kt", "*.kts"]),
                            config.test_patterns)

    facts_before = parse_files(repo, rng.base, [c.base_path for c in changes if c.base_path])
    facts_after = parse_files(repo, rng.head, [c.head_path for c in changes if c.head_path])
    names = changed_symbol_names(facts_before, facts_after, renames)
    extra_paths, warning = find_impacted_files(repo, rng.head, names, set(facts_before) | set(facts_after), config)
    if warning:
        warnings.append(warning)
    parsed = set(facts_before) | set(facts_after)
    extra_paths = sorted(set(extra_paths) | (_import_neighbours(facts_after, index_after, mods_after) - parsed))
    facts_extra = parse_files(repo, rng.head, extra_paths)
    for facts in (facts_before, facts_after, facts_extra):
        warnings += [f"{path}: {f.error}" for path, f in sorted(facts.items()) if f.error]

    before = build_graph({**facts_extra, **facts_before}, mods_before, index_before)
    after = build_graph({**facts_extra, **facts_after}, mods_after, index_after)
    cyclic = {n for comp in strongly_connected((e.src, e.dst) for e in after.edges if e.kind == "imports") for n in comp}
    delta = compute_delta(before, after, renames, prune_paths={p for p in facts_extra if file_id(p) not in cyclic})
    ctx = AnalysisContext(repo, rng, config, changes, before, after, delta, mods_before, mods_after,
                          facts_before, facts_after)
    findings = run_analyses(ctx)
    if any(CONFIG_FILE in (c.base_path, c.head_path) for c in all_changes):
        findings.append(Finding("analysis-config", Severity.YELLOW, f"Analysis config changed: {CONFIG_FILE}",
                                "Findings use the base version; review the new rules and ignore patterns."))
        findings.sort(key=lambda f: (-SEVERITY_RANK[f.severity], f.rule, f.title))
    overview = build_overview(ctx, findings, repo.numstat(rng, pathspecs))
    return RunResult(rng, changes, before, after, delta, findings, mods_before, mods_after, facts_before,
                     facts_after, warnings, ctx.extras.get("owners", {}), overview)
