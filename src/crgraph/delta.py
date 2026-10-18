"""Comparing the before and after graphs."""
from __future__ import annotations

from collections import defaultdict

from .model import CONTAINER_KINDS, EdgeDelta, Graph, GraphDelta, Node, NodeDelta, Status

CHANGED = frozenset({Status.ADDED, Status.REMOVED, Status.MODIFIED, Status.MOVED})


def rename_id(node_id: str | None, renames: dict[str, str]) -> str | None:
    """Rewrite a before-graph id to the path it has after the change."""
    if node_id is None:
        return None
    if node_id.startswith("file:"):
        path = node_id[5:]
        return "file:" + renames.get(path, path)
    if node_id.startswith("sym:"):
        path, _, rest = node_id[4:].rpartition("#")
        return f"sym:{renames.get(path, path)}#{rest}"
    return node_id


def _own_status(b: Node | None, a: Node | None, renamed: bool) -> Status:
    if b is None:
        return Status.ADDED
    if a is None:
        return Status.REMOVED
    if a.kind == "module":
        return Status.UNCHANGED
    if a.kind == "file":
        changed = a.body_hash != b.body_hash
    elif a.kind in CONTAINER_KINDS:
        changed = a.signature != b.signature
    else:
        changed = a.signature != b.signature or a.body_hash != b.body_hash
    if changed:
        return Status.MODIFIED
    return Status.MOVED if renamed else Status.UNCHANGED


def compute_delta(before: Graph, after: Graph, renames: dict[str, str],
                  prune_paths: set[str] | frozenset[str] = frozenset()) -> GraphDelta:
    old_path_of = {new: old for old, new in renames.items()}
    bmap = {rename_id(nid, renames): n for nid, n in before.nodes.items()}
    nodes: dict[str, NodeDelta] = {}
    for nid in bmap.keys() | after.nodes.keys():
        b, a = bmap.get(nid), after.nodes.get(nid)
        ref = a or b
        renamed = a is not None and b is not None and a.path in old_path_of
        nodes[nid] = NodeDelta(
            id=nid, kind=ref.kind, name=ref.name, status=_own_status(b, a, renamed),
            parent=a.parent if a else rename_id(b.parent, renames), path=ref.path, module=ref.module,
            lang=ref.lang, before=b, after=a, from_path=old_path_of.get(ref.path) if renamed else None,
        )
    alias = _pair_moved_symbols(nodes)
    alias.update(_pair_arity_changes(nodes))
    for n in nodes.values():  # e.g. a member removed from a class that moved keeps living inside the moved class
        if n.parent in alias:
            n.parent = alias[n.parent]
    own = {nid: n.status for nid, n in nodes.items()}  # before containers inherit their children's changes
    children: dict[str, list[str]] = defaultdict(list)
    for n in nodes.values():
        if n.parent in nodes:
            children[n.parent].append(n.id)

    def depth(nid: str) -> int:
        d, cur = 0, nodes[nid].parent
        while cur in nodes:
            d, cur = d + 1, nodes[cur].parent
        return d

    order = sorted(nodes, key=depth, reverse=True)
    for nid in order:  # bottom-up: containers with changed children are modified
        n = nodes[nid]
        if n.status == Status.UNCHANGED and any(nodes[c].status in CHANGED for c in children[nid]):
            n.status = Status.MODIFIED

    def before_id(nid: str) -> str | None:
        nid = rename_id(nid, renames)
        return alias.get(nid, nid)

    bedges = {(before_id(e.src), before_id(e.dst), e.kind): e for e in before.edges}
    aedges = {(e.src, e.dst, e.kind): e for e in after.edges}
    edges: list[EdgeDelta] = []
    for key in sorted(bedges.keys() | aedges.keys()):
        e = aedges.get(key) or bedges[key]
        status = Status.UNCHANGED if key in aedges and key in bedges else (Status.ADDED if key in aedges else Status.REMOVED)
        edges.append(EdgeDelta(key[0], key[1], key[2], e.confidence, e.derived, status))

    for e in edges:  # one hop of impact: symbol-level uses of changed symbols, or imports of removed files
        if e.kind == "depends_on" or e.src not in nodes or e.dst not in nodes:
            continue
        dst_own = own.get(e.dst, nodes[e.dst].status)
        if e.kind == "imports":
            hits = dst_own == Status.REMOVED
        else:
            hits = dst_own in CHANGED and (e.status != Status.REMOVED or dst_own == Status.REMOVED)
        if hits and nodes[e.src].status == Status.UNCHANGED:
            nodes[e.src].status = Status.IMPACTED
    for nid in order:
        n = nodes[nid]
        if n.status == Status.UNCHANGED and any(nodes[c].status == Status.IMPACTED for c in children[nid]):
            n.status = Status.IMPACTED

    drop: set[str] = set()
    for path in prune_paths:
        fid = f"file:{path}"
        if fid in nodes and nodes[fid].status == Status.UNCHANGED:
            stack = [fid]
            while stack:
                cur = stack.pop()
                drop.add(cur)
                stack.extend(children[cur])
    for nid in drop:
        del nodes[nid]
    edges = [e for e in edges if e.src not in drop and e.dst not in drop]
    return GraphDelta(nodes, edges)


def _pair_moved_symbols(nodes: dict[str, NodeDelta]) -> dict[str, str]:
    """A top-level symbol removed from one file and added unchanged to another is one moved symbol.

    Returns the before-id -> after-id aliases of the merged nodes.
    """
    alias: dict[str, str] = {}
    added: dict[tuple[str, str, str | None], list[NodeDelta]] = defaultdict(list)
    for n in nodes.values():
        if n.status == Status.ADDED and n.id.startswith("sym:") and (n.parent or "").startswith("file:"):
            added[(n.kind, n.name, n.after.signature)].append(n)
    for r in [n for n in nodes.values() if n.status == Status.REMOVED and n.id.startswith("sym:")
              and (n.parent or "").startswith("file:")]:
        candidates = added.get((r.kind, r.name, r.before.signature))
        if not candidates:
            continue
        a = candidates.pop(0)
        old_prefix, new_prefix = r.id.split("#", 1)[0] + "#", a.id.split("#", 1)[0] + "#"
        qual = r.id.split("#", 1)[1].rpartition("(")[0]
        for old_id in [nid for nid in nodes if nid.startswith(old_prefix) and _within(nid, old_prefix, qual)]:
            new_id = new_prefix + old_id[len(old_prefix):]
            old, new = nodes.get(old_id), nodes.get(new_id)
            if old is None or new is None or old.status != Status.REMOVED or new.status != Status.ADDED:
                continue
            new.before = old.before
            new.from_path = old.path
            new.status = Status.MOVED if _own_status(old.before, new.after, True) != Status.MODIFIED else Status.MODIFIED
            alias[old_id] = new_id
            del nodes[old_id]
    return alias


def _pair_arity_changes(nodes: dict[str, NodeDelta]) -> dict[str, str]:
    """A function whose parameter count changed (removed f(1), added f(2) in the same owner) is one modified node.

    Returns the before-id -> after-id aliases, so before edges follow the merged node.
    """
    def key(n: NodeDelta) -> tuple[str | None, str, str]:
        return n.parent, n.kind, n.name

    removed, added = defaultdict(list), defaultdict(list)
    for n in nodes.values():
        if n.id.startswith("sym:") and n.kind not in CONTAINER_KINDS:
            if n.status == Status.REMOVED:
                removed[key(n)].append(n)
            elif n.status == Status.ADDED:
                added[key(n)].append(n)
    alias: dict[str, str] = {}
    for k, olds in removed.items():
        news = added.get(k, [])
        if len(olds) != 1 or len(news) != 1:
            continue  # overloads: pairing would be a guess
        old, new = olds[0], news[0]
        new.before, new.status = old.before, Status.MODIFIED
        new.from_path = old.from_path
        alias[old.id] = new.id
        del nodes[old.id]
    return alias


def _within(node_id: str, prefix: str, qual: str) -> bool:
    rest = node_id[len(prefix):].rpartition("(")[0]
    return rest == qual or rest.startswith(qual + ".")
