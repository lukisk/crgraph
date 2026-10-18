"""Module/file structure checks: cycles, rule violations, dependency changes, coupling, splittability."""
from __future__ import annotations

from collections import Counter, defaultdict

from ..config import ForbidRule, LayersRule
from ..globs import glob_match
from ..graphalg import strongly_connected
from ..model import Finding, NodeDelta, Severity, Status, edge_id, file_id, module_id
from . import AnalysisContext


def _pairs(ctx: AnalysisContext, kind: str, side: str) -> set[tuple[str, str]]:
    skip = Status.ADDED if side == "before" else Status.REMOVED
    return {(e.src, e.dst) for e in ctx.delta.edges if e.kind == kind and e.status != skip}


def _file_pairs(ctx: AnalysisContext, side: str) -> set[tuple[str, str]]:
    """File-to-file dependencies: imports plus symbol-level uses lifted to their files."""
    skip = Status.ADDED if side == "before" else Status.REMOVED
    nodes = ctx.delta.nodes
    pairs = set()
    for e in ctx.delta.edges:
        if e.status == skip or e.kind == "depends_on" or e.src not in nodes or e.dst not in nodes:
            continue
        a, b = nodes[e.src].path, nodes[e.dst].path
        if a and b and a != b:
            pairs.add((file_id(a), file_id(b)))
    return pairs


def _name(ctx: AnalysisContext, node_id: str) -> str:
    n = ctx.delta.nodes.get(node_id)
    if n is None:
        return node_id
    return n.path if n.kind == "file" and n.path else n.name


def check_new_cycles(ctx: AnalysisContext) -> list[Finding]:
    findings = []
    levels = (("depends_on", "module", _pairs(ctx, "depends_on", "before"), _pairs(ctx, "depends_on", "after")),
              ("imports", "file", _file_pairs(ctx, "before"), _file_pairs(ctx, "after")))
    for kind, label, before_pairs, after_pairs in levels:
        comp_of = {n: i for i, comp in enumerate(strongly_connected(before_pairs)) for n in comp}
        for comp in strongly_connected(after_pairs):
            closing = [(a, b) for a, b in sorted(after_pairs - before_pairs) if a in comp and b in comp
                       and (a not in comp_of or comp_of.get(a) != comp_of.get(b))]
            if not closing:
                continue  # every edge in this group existed before, or only joined nodes already cyclic
            a, b = closing[0]
            cycle = [a, *_shortest_path(b, a, {(x, y) for x, y in after_pairs if x in comp and y in comp})]
            names = [_name(ctx, n) for n in cycle]
            detail = f"Introduced by the new dependency {_name(ctx, a)} → {_name(ctx, b)}."
            if len(comp) > len(cycle) - 1:
                detail += f" {len(comp)} {label}s are now mutually dependent."
            # file-level pairs are lifted from symbol edges, so only module edges have real edge ids
            edges = [edge_id(x, y, kind) for x, y in zip(cycle, cycle[1:], strict=False)] if kind == "depends_on" else []
            findings.append(Finding("new-cycle", Severity.RED, f"New {label} cycle: {' → '.join(names)}", detail,
                                    sorted(set(cycle)) + edges))
    return findings


def _shortest_path(start: str, goal: str, pairs: set[tuple[str, str]]) -> list[str]:
    """Nodes on a shortest path start → … → goal (inclusive), by BFS."""
    adj: dict[str, list[str]] = defaultdict(list)
    for x, y in sorted(pairs):
        adj[x].append(y)
    prev: dict[str, str] = {}
    queue, seen = [start], {start}
    while queue:
        cur = queue.pop(0)
        if cur == goal:
            break
        for nxt in adj[cur]:
            if nxt not in seen:
                seen.add(nxt)
                prev[nxt] = cur
                queue.append(nxt)
    path = [goal]
    while path[-1] != start:
        path.append(prev[path[-1]])
    return path[::-1]


def _endpoints(n: NodeDelta, ctx: AnalysisContext) -> list[str]:
    if n.kind == "module":
        return [n.name, f"{n.path}/" if n.path else ""]
    module = ctx.delta.nodes.get(module_id(n.module or ""))
    return [n.path or "", n.module or "", f"{module.path}/" if module and module.path else ""]


def _match(pattern: str, names: list[str]) -> bool:
    if pattern.startswith("!"):
        return not any(glob_match(pattern[1:], x) for x in names if x)
    return any(glob_match(pattern, x) for x in names if x)


def _layer(rule: LayersRule, names: list[str]) -> int | None:
    for i, layer in enumerate(rule.order):
        if _match(rule.match.replace("{layer}", layer), names):
            return i
    return None


def violates(rule: ForbidRule | LayersRule, src: list[str], dst: list[str]) -> bool:
    if isinstance(rule, ForbidRule):
        if rule.except_from and _match(rule.except_from, src):
            return False
        return _match(rule.src, src) and _match(rule.dst, dst)
    ls, ld = _layer(rule, src), _layer(rule, dst)
    return ls is not None and ld is not None and ls > ld


def check_rule_violations(ctx: AnalysisContext) -> list[Finding]:
    findings, reported = [], set()
    added = [e for e in ctx.delta.edges if e.status == Status.ADDED and e.kind in ("depends_on", "imports")]
    added.sort(key=lambda e: e.kind != "depends_on")  # module-level first, file-level adds detail
    for e in added:
        s, d = ctx.delta.nodes.get(e.src), ctx.delta.nodes.get(e.dst)
        if s is None or d is None:
            continue
        for i, rule in enumerate(ctx.config.rules):
            if not violates(rule, _endpoints(s, ctx), _endpoints(d, ctx)):
                continue
            across = e.kind == "depends_on" or s.module != d.module
            key = (i, s.module, d.module) if across else (i, e.src, e.dst)
            if key in reported:
                continue
            reported.add(key)
            findings.append(Finding(
                "rule-violation", Severity.RED, f"Rule violated: {rule.describe()}",
                f"{_name(ctx, e.src)} → {_name(ctx, e.dst)}", [e.src, e.dst, e.id]))
    return findings


def check_module_deps(ctx: AnalysisContext) -> list[Finding]:
    before, after = _pairs(ctx, "depends_on", "before"), _pairs(ctx, "depends_on", "after")
    derived = {(e.src, e.dst) for e in ctx.delta.edges if e.kind == "depends_on" and e.derived}
    findings = []
    for src, dst in sorted(after - before):
        via = " (via imports, not declared in a build file)" if (src, dst) in derived else ""
        findings.append(Finding("module-dep-added", Severity.YELLOW,
                                f"New dependency {_name(ctx, src)} → {_name(ctx, dst)}{via}", "",
                                [src, dst, edge_id(src, dst, "depends_on")]))
    for src, dst in sorted(before - after):
        findings.append(Finding("module-dep-removed", Severity.INFO,
                                f"Removed dependency {_name(ctx, src)} → {_name(ctx, dst)}", "", [src, dst]))
    return findings


def check_coupling(ctx: AnalysisContext) -> list[Finding]:
    fan_before = Counter(s for s, _ in _pairs(ctx, "depends_on", "before"))
    fan_after = Counter(s for s, _ in _pairs(ctx, "depends_on", "after"))
    findings = []
    for module, count in sorted(fan_after.items()):
        was = fan_before.get(module, 0)
        if count - was >= ctx.config.thresholds.fanout_delta:
            findings.append(Finding("coupling-increase", Severity.YELLOW,
                                    f"{_name(ctx, module)} now depends on {count} modules (was {was})", "", [module]))
    return findings


def check_splittable(ctx: AnalysisContext) -> list[Finding]:
    changed = {module_id(n.module) for n in ctx.delta.nodes.values()
               if n.kind == "file" and n.module and n.lang not in (None, "other")
               and n.status in (Status.ADDED, Status.REMOVED, Status.MODIFIED, Status.MOVED)}
    if len(changed) < 2:
        return []
    adj: dict[str, set[str]] = defaultdict(set)
    for a, b in _pairs(ctx, "depends_on", "before") | _pairs(ctx, "depends_on", "after"):
        if a in changed and b in changed:
            adj[a].add(b)
            adj[b].add(a)
    groups, seen = [], set()
    for start in sorted(changed):
        if start in seen:
            continue
        group, stack = set(), [start]
        while stack:
            cur = stack.pop()
            if cur not in group:
                group.add(cur)
                stack.extend(adj[cur] - group)
        seen |= group
        groups.append(sorted(group))
    ctx.extras["module_groups"] = len(groups)
    if len(groups) < 2:
        return []
    detail = "\n".join(f"group {i + 1}: " + ", ".join(_name(ctx, m) for m in g) for i, g in enumerate(groups))
    return [Finding("splittable", Severity.INFO,
                    f"Change touches {len(groups)} independent groups of modules — consider splitting",
                    detail, sorted(changed))]


CHECKS = [check_new_cycles, check_rule_violations, check_module_deps, check_coupling, check_splittable]
