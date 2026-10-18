"""The overall picture of a change: verdict, size, per-module table and where to start reviewing."""
from __future__ import annotations

from collections import Counter, defaultdict
from typing import Any

from .analyze import AnalysisContext
from .globs import match_any
from .lang import plugin_for
from .model import SYMBOL_KINDS, Finding, NodeDelta, Severity, Status

CHANGED = (Status.ADDED, Status.REMOVED, Status.MODIFIED, Status.MOVED)
KIND_WORDS = {Status.ADDED: "new code", Status.REMOVED: "removed code",
              Status.MODIFIED: "changes to existing code", Status.MOVED: "moved code"}
USE_KINDS = ("calls", "references", "inherits")
SIZES = ["S", "M", "L", "XL"]
START_HERE = 5


def size_label(code_lines: int, modules: int) -> str:
    """S/M/L/XL from changed production lines and the number of modules with code changes."""
    level = 0 if code_lines < 200 else 1 if code_lines < 800 else 2 if code_lines < 2000 else 3
    if modules > 1:
        level = max(level, 1)
    if modules > 3:
        level = max(level, 2)
    return SIZES[level]


def _lines() -> dict[str, int]:
    return {"added": 0, "deleted": 0}


def build_overview(ctx: AnalysisContext, findings: list[Finding],
                   numstat: dict[str, tuple[int, int] | None]) -> dict[str, Any]:
    nodes = ctx.delta.nodes
    is_test = lambda path: match_any(ctx.config.test_patterns, path)  # noqa: E731
    paths = {c.head_path or c.path for c in ctx.changes}
    module_of = {n.path: n.module for n in nodes.values() if n.kind == "file" and n.path}

    def mod(path: str) -> str:
        return module_of.get(path) or ctx.mods_after.module_of(path)

    # size: lines by production code / tests / everything else
    size = {"code": _lines(), "tests": _lines(), "other": _lines(), "binary_files": 0}
    mod_lines: dict[str, dict[str, dict[str, int]]] = defaultdict(lambda: {"lines": _lines(), "test_lines": _lines()})
    code_modules: set[str] = set()
    for path, stat in sorted(numstat.items()):
        if path not in paths:
            continue
        if stat is None:
            size["binary_files"] += 1
            continue
        kind = "tests" if is_test(path) else "code" if plugin_for(path) is not None else "other"
        size[kind]["added"] += stat[0]
        size[kind]["deleted"] += stat[1]
        if kind != "other":
            bucket = mod_lines[mod(path)]["test_lines" if kind == "tests" else "lines"]
            bucket["added"] += stat[0]
            bucket["deleted"] += stat[1]
        if kind == "code":
            code_modules.add(mod(path))
    written = size["code"]["added"] + size["tests"]["added"]
    size["test_share"] = round(100 * size["tests"]["added"] / written) if written else None

    # symbols by status, production code only
    sym_counts: dict[str, Counter[str]] = defaultdict(Counter)
    for n in nodes.values():
        if n.kind in SYMBOL_KINDS and n.status in CHANGED and not is_test(n.path or ""):
            sym_counts[n.module or ""][n.status.value] += 1
            code_modules.add(n.module or "")

    # findings per module and per file
    def target_paths(f: Finding) -> set[str]:
        return {nodes[t].path for t in f.targets if t in nodes and nodes[t].path}

    file_findings: dict[str, Counter[str]] = defaultdict(Counter)
    mod_findings: dict[str, Counter[str]] = defaultdict(Counter)
    for f in findings:
        if f.severity == Severity.INFO:
            continue
        for p in target_paths(f):
            file_findings[p][f.severity.value] += 1
        for m in {nodes[t].module for t in f.targets if t in nodes and nodes[t].module}:
            mod_findings[m][f.severity.value] += 1

    used_by = Counter(e.dst for e in ctx.after.edges if e.kind == "depends_on")
    rows = []
    for n in nodes.values():
        if n.kind != "module" or n.status == Status.UNCHANGED:
            continue
        mid = n.id[len("module:"):]
        files = sum(1 for f in nodes.values() if f.kind == "file" and f.module == mid and f.status in CHANGED)
        rows.append({
            "id": n.id, "name": n.name, "status": n.status.value, "files": files,
            "symbols": {s: sym_counts[mid][s] for s in ("added", "removed", "modified", "moved")},
            **mod_lines[mid],
            "findings": {s: mod_findings[mid][s] for s in ("red", "yellow")},
            "used_by": used_by[n.id],
        })
    weight = lambda r: sum(r["lines"].values()) + sum(r["test_lines"].values())  # noqa: E731
    rows.sort(key=lambda r: (-weight(r), -r["files"], r["name"]))
    # modules (often plain directories) where only docs/config changed are listed by name only
    quiet = [r for r in rows if r["status"] != "impacted" and not weight(r) and not any(r["symbols"].values())
             and not any(r["findings"].values())]

    groups = ctx.extras.get("module_groups", 1)
    code_lines = size["code"]["added"] + size["code"]["deleted"]
    return {
        "verdict": _verdict(ctx, findings, nodes, rows, code_modules, sym_counts, size, paths, is_test, groups),
        "size_label": size_label(code_lines, len(code_modules)) if paths else None,
        "size": size,
        "modules": [r for r in rows if r not in quiet],
        "other_modules": [r["name"] for r in quiet],
        "start_here": _start_here(ctx, nodes, numstat, file_findings, is_test),
    }


def _verdict(ctx, findings, nodes, rows, code_modules, sym_counts, size, paths, is_test, groups) -> str:
    if not paths:
        return "No changes."
    if not code_modules:
        tests = [p for p in paths if is_test(p)]
        if tests and size["code"] == _lines():
            return f"Tests only: {len(tests)} test file(s) changed."
        return "No code changes: only docs, config or other files."
    total = Counter()
    for c in sym_counts.values():
        total.update(c)
    changed = sum(total.values()) or 1
    if all(total[s] >= 0.2 * changed for s in ("added", "removed", "moved")):
        kind = "restructuring (moved, added and removed code)"
    elif sum(total.values()):
        kind = KIND_WORDS[max(CHANGED, key=lambda s: (total[s.value], -CHANGED.index(s)))]
    else:
        kind = KIND_WORDS[Status.MODIFIED]
    names = [r["name"] for r in rows if r["id"][len("module:"):] in code_modules]
    where = (f"in {names[0]}" if len(names) == 1 else f"in {names[0]} and {names[1]}" if len(names) == 2
             else f"across {len(names)} modules")
    extras = []
    real = ctx.mods_after.modules
    added_modules = sum(1 for r in rows if r["status"] == "added"
                        and getattr(real.get(r["id"][len("module:"):]), "system", "fallback") != "fallback")
    if added_modules:
        extras.append(f"adds {added_modules} new module(s)")
    api_users = {nodes[t].module for f in findings if f.rule == "api-change" and f.severity == Severity.RED
                 for t in f.targets[1:] if t in nodes} - {None}
    api_users -= {nodes[f.targets[0]].module for f in findings if f.rule == "api-change" and f.targets[0] in nodes}
    if api_users:
        extras.append(f"changes public API used by {len(api_users)} other module(s)")
    cycles = sum(1 for f in findings if f.rule == "new-cycle")
    if cycles:
        extras.append(f"introduces {cycles} new cycle(s)")
    if groups >= 2:
        extras.append(f"touches {groups} independent groups of modules")
    return f"Mostly {kind} {where}" + "".join(f"; {e}" for e in extras) + "."


def _start_here(ctx, nodes: dict[str, NodeDelta], numstat, file_findings, is_test) -> list[dict[str, Any]]:
    files = [n for n in nodes.values() if n.kind == "file" and n.status in CHANGED and n.status != Status.REMOVED]
    code = [n for n in files if plugin_for(n.path or "") is not None]  # docs, scripts and config are not where to start
    prod = [n for n in code if not is_test(n.path or "")]
    files = prod or code or files
    callers: dict[str, set[str]] = defaultdict(set)
    for e in ctx.delta.edges:
        src, dst = nodes.get(e.src), nodes.get(e.dst)
        if (e.kind in USE_KINDS and src and dst and src.path and dst.path and src.path != dst.path
                and not is_test(src.path)):
            callers[dst.path].add(src.path)
    complexity: Counter[str] = Counter()
    for n in nodes.values():
        if n.kind == "function" and n.path and n.status in CHANGED:
            complexity[n.path] += (n.after.complexity if n.after else 0) - (n.before.complexity if n.before else 0)
    hotspots = ctx.extras.get("hotspots", {})
    ranked = []
    for n in files:
        path = n.path or ""
        red, yellow = file_findings[path]["red"], file_findings[path]["yellow"]
        users, cx, hot = len(callers[path]), complexity[path], path in hotspots
        lines = sum(numstat.get(path) or (0, 0))
        score = red * 5 + yellow * 2 + min(users, 20) * 0.5 + min(max(cx, 0), 10) * 0.3 + (3 if hot else 0) \
            + min(lines / 100, 5)
        reasons = [r for r, show in ((f"{red} red finding(s)", red), (f"{yellow} yellow finding(s)", yellow),
                                     (f"used from {users} other file(s)", users), (f"complexity +{cx}", cx > 0),
                                     ("hotspot", hot), (f"{lines} line(s) changed", lines)) if show]
        ranked.append({"path": path, "module": n.module, "score": round(score, 1), "reasons": reasons})
    ranked.sort(key=lambda r: (-r["score"], r["path"]))
    return ranked[:START_HERE]
