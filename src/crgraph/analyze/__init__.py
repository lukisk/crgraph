"""Findings: architecture-relevant observations about a change."""
from __future__ import annotations

import importlib
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from ..config import Config
from ..gitio import FileChange, RangeSpec, Repo
from ..model import SEVERITY_RANK, FileFacts, Finding, Graph, GraphDelta
from ..modules import ModuleMap


@dataclass
class AnalysisContext:
    repo: Repo
    rng: RangeSpec
    config: Config
    changes: list[FileChange]
    before: Graph
    after: Graph
    delta: GraphDelta
    mods_before: ModuleMap
    mods_after: ModuleMap
    facts_before: dict[str, FileFacts]
    facts_after: dict[str, FileFacts]
    extras: dict[str, Any] = field(default_factory=dict)  # side outputs for renderers, e.g. "owners"


Check = Callable[[AnalysisContext], list[Finding]]
CHECK_MODULES: tuple[str, ...] = ("structure", "symbols", "surface", "history")  # each defines CHECKS: list[Check]


def all_checks() -> list[Check]:
    checks: list[Check] = []
    for name in CHECK_MODULES:
        checks += importlib.import_module(f"{__name__}.{name}").CHECKS
    return checks


def run_analyses(ctx: AnalysisContext) -> list[Finding]:
    findings: list[Finding] = []
    for check in all_checks():
        findings.extend(check(ctx))
    findings.sort(key=lambda f: (-SEVERITY_RANK[f.severity], f.rule, f.title))
    return findings
