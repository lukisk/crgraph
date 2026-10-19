"""delta.json: the stable, code-free description of a change (schema_version 1)."""
from __future__ import annotations

import json
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from ..model import Status
from ..pipeline import RunResult

SCHEMA_VERSION = 1


def _bucket(kind: str) -> str:
    return "modules" if kind == "module" else ("files" if kind == "file" else "symbols")


def stats(result: RunResult) -> dict[str, Any]:
    counts: dict[str, Counter[str]] = {"modules": Counter(), "files": Counter(), "symbols": Counter()}
    for n in result.delta.nodes.values():
        counts[_bucket(n.kind)][n.status.value] += 1
    return {
        **{k: dict(sorted(v.items())) for k, v in counts.items()},
        "findings": dict(Counter(f.severity.value for f in result.findings)),
        "impacted_modules": sum(1 for n in result.delta.nodes.values()
                                if n.kind == "module" and n.status == Status.IMPACTED),
        "changed_paths": len(result.changes),
    }


def _relevant(result: RunResult) -> tuple[set[str], list[Any]]:
    """Drop unchanged symbols and edges that touch nothing changed or impacted (they only add size)."""
    nodes = result.delta.nodes
    unchanged = {nid for nid, n in nodes.items() if n.status == Status.UNCHANGED}
    edges = [e for e in result.delta.edges
             if e.status != Status.UNCHANGED or e.src not in unchanged or e.dst not in unchanged or e.kind == "depends_on"]
    keep = {nid for nid, n in nodes.items() if n.kind in ("module", "file") or nid not in unchanged}
    keep |= {t for f in result.findings for t in f.targets}
    keep |= {x for e in edges for x in (e.src, e.dst)}
    for nid in list(keep):  # keep the containment chain of every kept node
        parent = nodes[nid].parent if nid in nodes else None
        while parent in nodes and parent not in keep:
            keep.add(parent)
            parent = nodes[parent].parent
    return keep, [e for e in edges if e.src in keep and e.dst in keep]


def to_document(result: RunResult, *, depth: str = "module", explain: str | None = None) -> dict[str, Any]:
    keep, kept_edges = _relevant(result)
    nodes = []
    for n in sorted((result.delta.nodes[k] for k in keep if k in result.delta.nodes), key=lambda n: n.id):
        b, a = n.before, n.after
        nodes.append({
            "id": n.id, "kind": n.kind, "name": n.name, "parent": n.parent, "path": n.path,
            "from_path": n.from_path, "module": n.module, "lang": n.lang, "status": n.status.value,
            "test": n.id in result.tests,
            "visibility": (a or b).visibility,
            "signature_before": b.signature if b else None, "signature_after": a.signature if a else None,
            "loc_before": b.loc if b else None, "loc_after": a.loc if a else None,
            "complexity_before": b.complexity if b else None, "complexity_after": a.complexity if a else None,
        })
    # an edge's id is f"{src}->{dst}:{kind}" (model.edge_id); it is not repeated in the document
    edges = [{"src": e.src, "dst": e.dst, "kind": e.kind, "confidence": e.confidence,
              "derived": e.derived, "status": e.status.value} for e in kept_edges]
    findings = [{"id": f"F{i + 1}", "rule": f.rule, "severity": f.severity.value, "title": f.title,
                 "detail": f.detail, "targets": f.targets} for i, f in enumerate(result.findings)]
    return _displayable({
        "schema_version": SCHEMA_VERSION,
        "tool": "crgraph",
        "range": {"base": result.rng.base, "head": result.rng.head, "label": result.rng.label},
        "generated_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "ui": {"depth": depth},
        "stats": stats(result),
        "overview": result.overview,
        "nodes": nodes,
        "edges": edges,
        "findings": findings,
        "owners": result.owners,
        "warnings": list(result.warnings),
        "explain": explain,
    })


def _displayable(value: Any) -> Any:
    """Paths that are not valid UTF-8 reach us surrogate-escaped (gitio); show the bad bytes as \\xNN.

    Reversible on purpose: U+FFFD would give two such paths (and their node ids) the same name.
    """
    if isinstance(value, str):
        return value.encode("utf-8", "surrogateescape").decode("utf-8", "backslashreplace")
    if isinstance(value, dict):
        return {_displayable(k): _displayable(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_displayable(v) for v in value]
    return value


def to_json(doc: dict[str, Any]) -> str:
    return json.dumps(doc, indent=1, ensure_ascii=False) + "\n"


def write_json(doc: dict[str, Any], path: Path) -> None:
    path.write_text(to_json(doc))
