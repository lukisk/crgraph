"""Symbol-level checks: public API changes, blast radius, complexity, dead-on-arrival."""
from __future__ import annotations

from collections import defaultdict

from ..globs import match_any
from ..model import CONTAINER_KINDS, SYMBOL_KINDS, Finding, Severity, Status, module_id, qualname_of
from . import AnalysisContext

USE_KINDS = ("calls", "references", "inherits")
ENTRY_POINT_NAMES = frozenset({"main", "App", "Application", "index", "setup", "configure"})


def _incoming(ctx: AnalysisContext) -> tuple[dict[str, set[str]], dict[str, set[str]]]:
    before, after = defaultdict(set), defaultdict(set)
    for e in ctx.delta.edges:
        if e.kind in USE_KINDS:
            if e.status != Status.ADDED:
                before[e.dst].add(e.src)
            if e.status != Status.REMOVED:
                after[e.dst].add(e.src)
    return before, after


def check_api_changes(ctx: AnalysisContext) -> list[Finding]:
    used_before, used_after = _incoming(ctx)
    findings = []
    for n in sorted(ctx.delta.nodes.values(), key=lambda n: n.id):
        if n.kind not in SYMBOL_KINDS or n.before is None or n.before.visibility != "public":
            continue
        if match_any(ctx.config.test_patterns, n.path or ""):
            continue
        parent = ctx.delta.nodes.get(n.parent or "")
        if n.status == Status.REMOVED and parent is not None and parent.status == Status.REMOVED and parent.kind != "file":
            continue  # reported once, as part of its removed owner
        if n.status == Status.REMOVED:
            what = "removed"
        elif n.after is not None and n.after.visibility != "public":
            what = f"visibility reduced to {n.after.visibility}"
        elif n.after is not None and n.before.signature != n.after.signature:
            what = "signature changed"
        else:
            continue
        users = used_before[n.id] | used_after[n.id]
        outside = sorted(u for u in users if u in ctx.delta.nodes and ctx.delta.nodes[u].module != n.module)
        lines = [f"before: {n.before.signature}"]
        if what == "signature changed":
            lines.append(f"after:  {n.after.signature}")
        lines.append(f"used from {len(outside)} place(s) in other modules")
        findings.append(Finding("api-change", Severity.RED if outside else Severity.YELLOW,
                                f"Public API {what}: {qualname_of(n.id)}", "\n".join(lines), [n.id, *outside]))
    return findings


def check_blast_radius(ctx: AnalysisContext) -> list[Finding]:
    files = sorted(n.id for n in ctx.delta.nodes.values() if n.kind == "file" and n.status == Status.IMPACTED)
    modules = sorted(n.id for n in ctx.delta.nodes.values() if n.kind == "module" and n.status == Status.IMPACTED)
    if not files:
        return []
    severity = Severity.YELLOW if len(modules) > ctx.config.thresholds.blast_modules else Severity.INFO
    detail = "Impacted modules: " + (", ".join(ctx.delta.nodes[m].name for m in modules) or "none outside the change")
    return [Finding("blast-radius", severity,
                    f"{len(files)} unchanged file(s) in {len(modules)} other module(s) use changed code",
                    detail, modules + files)]


def check_complexity(ctx: AnalysisContext) -> list[Finding]:
    th = ctx.config.thresholds
    findings = []
    for n in sorted(ctx.delta.nodes.values(), key=lambda n: n.id):
        if n.kind != "function" or n.after is None or n.status not in (Status.ADDED, Status.MODIFIED, Status.MOVED):
            continue
        after = n.after.complexity
        if n.before is None:
            if after > th.complexity_max:
                findings.append(Finding("complexity", Severity.YELLOW,
                                        f"New function {qualname_of(n.id)} has complexity {after}", "", [n.id]))
            continue
        before = n.before.complexity
        if after - before >= th.complexity_delta or (after > th.complexity_max >= before):
            findings.append(Finding("complexity", Severity.YELLOW,
                                    f"Complexity of {qualname_of(n.id)} {before} → {after}", "", [n.id]))
    return findings


def check_dead_on_arrival(ctx: AnalysisContext) -> list[Finding]:
    _, used_after = _incoming(ctx)
    by_module: dict[str, list[str]] = defaultdict(list)
    for n in sorted(ctx.delta.nodes.values(), key=lambda n: n.id):
        if n.status != Status.ADDED or n.after is None or n.after.visibility != "public":
            continue
        if n.kind not in CONTAINER_KINDS | {"function"} or not (n.parent or "").startswith("file:"):
            continue
        if n.name in ENTRY_POINT_NAMES or match_any(ctx.config.test_patterns, n.path or ""):
            continue
        if not used_after[n.id]:
            by_module[n.module or ""].append(n.id)
    return [Finding("dead-on-arrival", Severity.INFO,
                    f"{len(ids)} new public symbol(s) in {module} have no callers in the analysed code",
                    ", ".join(qualname_of(i) for i in ids), [module_id(module), *ids])
            for module, ids in sorted(by_module.items())]


CHECKS = [check_api_changes, check_blast_radius, check_complexity, check_dead_on_arrival]
