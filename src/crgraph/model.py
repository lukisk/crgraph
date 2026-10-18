"""Core data types shared by every pipeline stage."""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import StrEnum


class Status(StrEnum):
    ADDED = "added"
    REMOVED = "removed"
    MODIFIED = "modified"
    MOVED = "moved"
    IMPACTED = "impacted"
    UNCHANGED = "unchanged"


class Severity(StrEnum):
    RED = "red"
    YELLOW = "yellow"
    INFO = "info"


SEVERITY_RANK = {Severity.INFO: 0, Severity.YELLOW: 1, Severity.RED: 2}
CONTAINER_KINDS = frozenset({"class", "interface", "object", "enum", "extension"})
SYMBOL_KINDS = CONTAINER_KINDS | {"function", "property"}


def module_id(mid: str) -> str:
    return f"module:{mid}"


def file_id(path: str) -> str:
    return f"file:{path}"


def sym_id(path: str, qualname: str, arity: int) -> str:
    return f"sym:{path}#{qualname}({arity})"


def edge_id(src: str, dst: str, kind: str) -> str:
    return f"{src}->{dst}:{kind}"


def qualname_of(node_id: str) -> str:
    """'sym:a/B.kt#Outer.load(1)' -> 'Outer.load'; other ids are returned unchanged."""
    if not node_id.startswith("sym:"):
        return node_id
    return re.sub(r"\(\d+\)$", "", node_id.rpartition("#")[2])


@dataclass(frozen=True)
class Symbol:
    qualname: str
    name: str
    kind: str  # class|interface|object|enum|extension|function|property
    visibility: str  # public|internal|private
    signature: str
    body_hash: str
    complexity: int
    loc: int
    arity: int = 0
    parent: str | None = None  # qualname of the enclosing symbol
    supertypes: tuple[str, ...] = ()


@dataclass(frozen=True)
class Ref:
    kind: str  # call|type|inherit
    name: str
    scope: str | None  # qualname of the enclosing symbol, None = file level


@dataclass
class FileFacts:
    path: str
    lang: str  # plugin name, or "other" for files without a language plugin
    package: str | None = None
    imports: list[str] = field(default_factory=list)
    symbols: list[Symbol] = field(default_factory=list)
    refs: list[Ref] = field(default_factory=list)
    file_hash: str = ""
    loc: int = 0
    error: str | None = None


@dataclass
class Node:
    id: str
    kind: str  # module|file|<symbol kind>
    name: str
    parent: str | None = None
    path: str | None = None
    module: str | None = None
    lang: str | None = None
    visibility: str = "public"
    signature: str | None = None
    body_hash: str | None = None
    complexity: int = 0
    loc: int = 0


@dataclass(frozen=True)
class Edge:
    src: str
    dst: str
    kind: str  # depends_on|imports|calls|references|inherits
    confidence: str = "exact"  # exact|heuristic
    derived: bool = False


@dataclass
class Graph:
    nodes: dict[str, Node] = field(default_factory=dict)
    edges: set[Edge] = field(default_factory=set)

    def add(self, node: Node) -> Node:
        return self.nodes.setdefault(node.id, node)

    def link(self, edge: Edge) -> None:
        if edge.src != edge.dst:
            self.edges.add(edge)


@dataclass
class NodeDelta:
    id: str
    kind: str
    name: str
    status: Status
    parent: str | None
    path: str | None
    module: str | None
    lang: str | None
    before: Node | None
    after: Node | None
    from_path: str | None = None


@dataclass(frozen=True)
class EdgeDelta:
    src: str
    dst: str
    kind: str
    confidence: str
    derived: bool
    status: Status

    @property
    def id(self) -> str:
        return edge_id(self.src, self.dst, self.kind)


@dataclass
class GraphDelta:
    nodes: dict[str, NodeDelta]
    edges: list[EdgeDelta]


@dataclass
class Finding:
    rule: str
    severity: Severity
    title: str
    detail: str = ""
    targets: list[str] = field(default_factory=list)
