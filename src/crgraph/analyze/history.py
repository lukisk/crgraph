"""History-based checks: hotspots (churn × complexity) and code owners."""
from __future__ import annotations

from collections import defaultdict

from ..globs import glob_match
from ..model import Finding, Severity, Status, file_id
from . import AnalysisContext

CODEOWNERS_PATHS = ["CODEOWNERS", ".github/CODEOWNERS", ".gitlab/CODEOWNERS", "docs/CODEOWNERS"]
HOTSPOT_SINCE = "6 months ago"
HOTSPOT_MIN_COMMITS = 3
HOTSPOT_MIN_COMPLEXITY = 20


def check_hotspots(ctx: AnalysisContext) -> list[Finding]:
    changed = [n for n in ctx.delta.nodes.values() if n.kind == "file" and n.status in (Status.MODIFIED, Status.MOVED)]
    if not changed:
        return []
    counts = ctx.repo.commit_counts(ctx.rng.head, HOTSPOT_SINCE)
    if not counts:
        return []
    ranked = sorted(counts.values())
    p90 = ranked[int(0.9 * (len(ranked) - 1))]
    complexity: dict[str, int] = defaultdict(int)
    for node in ctx.after.nodes.values():
        if node.kind == "function" and node.path:
            complexity[node.path] += node.complexity
    hot = [(n, counts.get(n.path or "", 0), complexity[n.path or ""]) for n in changed]
    hot = [h for h in hot if h[1] >= max(p90, HOTSPOT_MIN_COMMITS) and h[2] >= HOTSPOT_MIN_COMPLEXITY]
    if not hot:
        return []
    ctx.extras["hotspots"] = {n.path: c for n, c, _ in hot}
    detail = "\n".join(f"{n.path}: {c} commits in the last 6 months, complexity {x}" for n, c, x in hot)
    return [Finding("hotspot", Severity.INFO, f"{len(hot)} changed file(s) are hotspots (frequently changed and complex)",
                    detail, [n.id for n, _, _ in hot])]


def parse_codeowners(text: str) -> list[tuple[str, list[str]]]:
    rules = []
    for line in text.splitlines():
        line = line.split(" #", 1)[0].strip()
        if not line or line.startswith(("#", "[", "^[")):
            continue
        pattern, *owners = line.split()
        owners = [o for o in owners if "@" in o]
        if owners:
            rules.append((pattern, owners))
    return rules


def _codeowners_match(pattern: str, path: str) -> bool:
    anchored = pattern.startswith("/") or "/" in pattern.rstrip("/")
    p = pattern.lstrip("/")
    if p.endswith("/"):
        p += "**"
    candidates = [p] if any(ch in p for ch in "*?") else [p, f"{p}/**"]
    if not anchored:
        candidates = [f"**/{c}" for c in candidates]
    return any(glob_match(c, path) for c in candidates)


def owners_for(path: str, rules: list[tuple[str, list[str]]]) -> list[str]:
    owners: list[str] = []
    for pattern, rule_owners in rules:  # last matching rule wins
        if _codeowners_match(pattern, path):
            owners = rule_owners
    return owners


def check_owners(ctx: AnalysisContext) -> list[Finding]:
    texts = ctx.repo.read_many(ctx.rng.head, CODEOWNERS_PATHS)
    raw = next((texts[p] for p in CODEOWNERS_PATHS if texts.get(p)), None)
    if raw is None:
        return []
    rules = parse_codeowners(raw.decode("utf-8", "replace"))
    owners_map = {c.path: owners_for(c.path, rules) for c in ctx.changes}
    ctx.extras["owners"] = {p: o for p, o in owners_map.items() if o}
    by_owner: dict[str, list[str]] = defaultdict(list)
    for path, owners in owners_map.items():
        for owner in owners:
            by_owner[owner].append(path)
    if not by_owner:
        return []
    detail = "\n".join(f"{owner}: {len(paths)} file(s)" for owner, paths in sorted(by_owner.items()))
    targets = sorted({file_id(p) for paths in by_owner.values() for p in paths if file_id(p) in ctx.delta.nodes})
    return [Finding("owners", Severity.INFO, f"Touches {len(by_owner)} owner group(s): {', '.join(sorted(by_owner))}",
                    detail, targets)]


CHECKS = [check_hotspots, check_owners]
