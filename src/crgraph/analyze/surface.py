"""Surface checks: test gaps, new external dependencies, contract/config surface changes."""
from __future__ import annotations

import re
from collections import defaultdict
from pathlib import PurePosixPath

from ..globs import match_any
from ..lang import plugin_for
from ..model import CONTAINER_KINDS, Finding, Severity, Status, file_id, module_id
from . import AnalysisContext

ENV_PATTERNS = [
    re.compile(r"System\.getenv\(\s*\"([^\"]+)\""),
    re.compile(r"os\.environ(?:\.get)?[\[(]\s*[\"']([^\"']+)"),
    re.compile(r"os\.getenv\(\s*[\"']([^\"']+)"),
    re.compile(r"process\.env\.([A-Za-z_][A-Za-z0-9_]*)"),
    re.compile(r"process\.env\[\s*[\"']([^\"']+)"),
    re.compile(r"ProcessInfo\.processInfo\.environment\[\s*\"([^\"]+)\""),
]
ROUTE_PATTERNS = [
    re.compile(r"\b(?:get|post|put|delete|patch|route)\s*\(\s*\"(/[^\"]*)\""),
    re.compile(r"@(?:Get|Post|Put|Delete|Patch|Request)Mapping\(\s*(?:value\s*=\s*|path\s*=\s*)?\"([^\"]+)\""),
    re.compile(r"\b(?:app|router)\.(?:get|post|put|delete|patch|use)\(\s*['\"](/[^'\"]*)"),
    re.compile(r"@\w+\.(?:get|post|put|delete|patch|route)\(\s*[\"'](/[^\"']*)"),
]
PERMISSION = re.compile(r"<uses-permission[^>]*android:name=\"([^\"]+)\"")
PLIST_KEY = re.compile(r"<key>([^<]+)</key>")
NEXT_ROUTE = re.compile(r"(^|/)app/(.+/)?route\.(ts|js)$")
CHANGED = (Status.ADDED, Status.MODIFIED, Status.MOVED)


def _dec(data: bytes | None) -> str:
    return data.decode("utf-8", "replace") if data else ""


def _found(patterns: list[re.Pattern[str]], text: str) -> set[str]:
    return {m.group(1) for p in patterns for m in p.finditer(text)}


def _set_change(label: str, before: set[str], after: set[str]) -> str:
    parts = []
    if after - before:
        parts.append(f"added {label}: " + ", ".join(sorted(after - before)))
    if before - after:
        parts.append(f"removed {label}: " + ", ".join(sorted(before - after)))
    return "; ".join(parts)


def check_test_gap(ctx: AnalysisContext) -> list[Finding]:
    sources: dict[str, list[str]] = defaultdict(list)
    tests: dict[str, list[str]] = defaultdict(list)
    for n in ctx.delta.nodes.values():
        if n.kind == "file" and n.status in CHANGED and n.lang not in (None, "other"):
            bucket = tests if match_any(ctx.config.test_patterns, n.path or "") else sources
            bucket[n.module or ""].append(n.path or "")
    findings = []
    for module, paths in sorted(sources.items()):
        test_stems = {PurePosixPath(p).stem.lower() for p in tests.get(module, [])}
        types = sorted({n.name for n in ctx.delta.nodes.values()
                        if n.kind in CONTAINER_KINDS and n.name != "Companion" and n.path in paths
                        and n.status in (Status.ADDED, Status.MODIFIED)})
        if not test_stems:
            findings.append(Finding("test-gap", Severity.YELLOW, f"No test changes in {module} ({len(paths)} changed source file(s))",
                                    ", ".join(types), [module_id(module), *(file_id(p) for p in sorted(paths))]))
            continue
        missing = [t for t in types if not any(t.lower() in stem for stem in test_stems)]
        if missing:
            findings.append(Finding("test-gap", Severity.YELLOW,
                                    f"Changed types without a matching test change in {module}: {', '.join(missing)}",
                                    "", [module_id(module)]))
    return findings


def check_external_deps(ctx: AnalysisContext) -> list[Finding]:
    findings = []
    for module, deps in sorted(ctx.mods_after.external.items()):
        new = deps - ctx.mods_before.external.get(module, set())
        if new:
            findings.append(Finding("external-dep-added", Severity.YELLOW,
                                    f"New external dependencies in {module}: {', '.join(sorted(new))}", "",
                                    [module_id(module)]))
    return findings


def check_contract_surface(ctx: AnalysisContext) -> list[Finding]:
    before = ctx.repo.read_many(ctx.rng.base, [c.base_path for c in ctx.changes if c.base_path])
    after = ctx.repo.read_many(ctx.rng.head, [c.head_path for c in ctx.changes if c.head_path])
    findings = []
    for c in ctx.changes:
        b = _dec(before.get(c.base_path)) if c.base_path else ""
        a = _dec(after.get(c.head_path)) if c.head_path else ""
        targets = [file_id(c.path)] if file_id(c.path) in ctx.delta.nodes else []
        verb = {"A": "added", "D": "removed", "R": "moved"}.get(c.status, "changed")
        if match_any(ctx.config.contract_files, c.path):
            detail = ""
            if c.path.endswith("AndroidManifest.xml"):
                detail = _set_change("permissions", _found([PERMISSION], b), _found([PERMISSION], a))
            elif c.path.endswith("Info.plist"):
                detail = _set_change("keys", _found([PLIST_KEY], b), _found([PLIST_KEY], a))
            findings.append(Finding("contract-surface", Severity.YELLOW, f"Contract file {verb}: {c.path}", detail, targets))
            continue
        if plugin_for(c.path) is None or match_any(ctx.config.test_patterns, c.path):
            continue
        if NEXT_ROUTE.search(c.path) and c.status in ("A", "D"):
            findings.append(Finding("contract-surface", Severity.YELLOW, f"HTTP route file {verb}: {c.path}", "", targets))
        for label, patterns in (("environment variables", ENV_PATTERNS), ("HTTP routes", ROUTE_PATTERNS)):
            detail = _set_change(label, _found(patterns, b), _found(patterns, a))
            if detail:
                findings.append(Finding("contract-surface", Severity.YELLOW, f"{label.capitalize()} changed in {c.path}",
                                        detail, targets))
    return findings


CHECKS = [check_test_gap, check_external_deps, check_contract_surface]
