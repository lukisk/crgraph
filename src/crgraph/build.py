"""Parsing files at a ref and building the module ⊃ file ⊃ symbol graph."""
from __future__ import annotations

from collections import defaultdict
from pathlib import PurePosixPath

from .gitio import Repo
from .globs import match_any
from .lang import parse_source, plugin_for
from .lang.base import raw_hash
from .model import Edge, FileFacts, Graph, Node, Ref, file_id, module_id, sym_id
from .modules import ModuleMap
from .progress import QUIET, Progress
from .resolve import RepoIndex

EDGE_KIND = {"call": "calls", "type": "references", "inherit": "inherits"}
MAX_CANDIDATES = 3


def parse_files(repo: Repo, ref: str, paths: list[str], progress: Progress = QUIET) -> dict[str, FileFacts]:
    """FileFacts for every readable path; files without a language plugin get lang 'other'."""
    facts: dict[str, FileFacts] = {}
    for path, data in repo.read_many(ref, paths).items():
        progress.advance()
        if data is None:
            continue
        plugin = plugin_for(path)
        if plugin is None:
            facts[path] = FileFacts(path=path, lang="other", file_hash=raw_hash(data), loc=data.count(b"\n") + 1)
        else:
            facts[path] = parse_source(plugin, path, data)
    return facts


def _ancestors(graph: Graph, node_id: str) -> set[str]:
    out, cur = set(), graph.nodes[node_id].parent
    while cur is not None:
        out.add(cur)
        cur = graph.nodes[cur].parent
    return out


class _Resolver:
    """Heuristic resolution of a by-name reference to symbol nodes."""

    def __init__(self, graph: Graph, facts: dict[str, FileFacts], imported: dict[str, set[str]],
                 test_patterns: list[str]):
        self.is_test = {path: match_any(test_patterns, path) for path in facts}
        self.graph = graph
        self.facts = facts
        self.imported = imported
        self.by_name: dict[str, list[str]] = defaultdict(list)
        for node in graph.nodes.values():
            if node.id.startswith("sym:"):
                self.by_name[node.name].append(node.id)
        self.names_used = {path: {r.name for r in f.refs} for path, f in facts.items()}

    def _file_private(self, node_id: str) -> bool:
        cur = self.graph.nodes.get(node_id)
        while cur is not None and cur.id.startswith("sym:"):
            if cur.visibility == "private" and cur.kind != "extension":  # several extensions of X share one id
                return True
            cur = self.graph.nodes.get(cur.parent) if cur.parent else None
        return False

    def resolve(self, ref: Ref, path: str) -> list[str]:
        g, f = self.graph, self.facts[path]
        candidates = []
        for cand in self.by_name.get(ref.name, []):
            node = g.nodes[cand]
            if node.kind == "extension":
                continue  # a type name means the type, never an extension of it (its members still resolve)
            if node.path != path and self._file_private(cand):
                continue  # private (or inside something private): not visible from other files
            parent = g.nodes[node.parent] if node.parent else None
            # a member is only reachable from files that mention its owner type (or from its own file)
            if parent is not None and parent.kind != "file" and node.path != path \
                    and parent.name not in self.names_used[path]:
                continue
            if self.is_test.get(node.path or "") and not self.is_test[path]:
                continue  # main code never depends on test sources
            candidates.append(cand)
        if len(candidates) <= 1:
            return candidates
        module = g.nodes[file_id(path)].module
        tiers = [
            [c for c in candidates if g.nodes[c].path == path],
            [c for c in candidates if g.nodes[c].path in self.imported[path]],
            [c for c in candidates if f.package and self.facts[g.nodes[c].path].package == f.package],
            [c for c in candidates if g.nodes[c].module == module],
            candidates,
        ]
        for tier in tiers:
            if tier:
                return tier if len(tier) <= MAX_CANDIDATES else []
        return []


def build_graph(facts: dict[str, FileFacts], modules: ModuleMap, index: RepoIndex) -> Graph:
    g = Graph()

    def ensure_module(mid: str) -> str:
        info = modules.modules[mid]
        return g.add(Node(module_id(mid), "module", mid, path=info.root, module=mid)).id

    for mid in list(modules.modules):
        ensure_module(mid)
    for src, dst in modules.deps:
        g.link(Edge(module_id(src), module_id(dst), "depends_on"))

    qual_ids: dict[str, dict[str, str]] = {}
    for path, f in sorted(facts.items()):
        mid = modules.module_of(path)
        parent = ensure_module(mid)
        fid = g.add(Node(file_id(path), "file", PurePosixPath(path).name, parent=parent, path=path, module=mid,
                         lang=f.lang, body_hash=f.file_hash, loc=f.loc)).id
        ids = qual_ids.setdefault(path, {})
        for s in f.symbols:
            sid = sym_id(path, s.qualname, s.arity)
            g.add(Node(sid, s.kind, s.name, parent=ids.get(s.parent, fid) if s.parent else fid, path=path,
                       module=mid, lang=f.lang, visibility=s.visibility, signature=s.signature,
                       body_hash=s.body_hash, complexity=s.complexity, loc=s.loc))
            ids.setdefault(s.qualname, sid)

    exact = {(e.src, e.dst) for e in g.edges}
    imported: dict[str, set[str]] = {}
    for path, f in facts.items():
        src_module = g.nodes[file_id(path)].module
        imported[path] = set()
        for target in index.resolve(f):
            for tf in target.files:
                imported[path].add(tf)
                if tf in facts:
                    g.link(Edge(file_id(path), file_id(tf), "imports"))
            for tm in target.modules:
                pair = (module_id(src_module), ensure_module(tm))
                if tm != src_module and pair not in exact:
                    g.link(Edge(*pair, "depends_on", derived=True))

    resolver = _Resolver(g, facts, imported, index.test_patterns)
    for path, f in facts.items():
        for ref in f.refs:
            src = qual_ids[path].get(ref.scope, file_id(path)) if ref.scope else file_id(path)
            src_up = _ancestors(g, src) | {src}
            for dst in resolver.resolve(ref, path):
                if dst in src_up or src in _ancestors(g, dst):
                    continue
                g.link(Edge(src, dst, EDGE_KIND[ref.kind], confidence="heuristic"))
    return g
