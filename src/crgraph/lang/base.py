"""Shared tree-sitter helpers, the language plugin protocol and the fact collector."""
from __future__ import annotations

import hashlib
import re
from contextvars import ContextVar
from typing import Protocol

import tree_sitter_language_pack as tslp
from tree_sitter import Node as TSNode
from tree_sitter import Parser

from ..model import FileFacts, Ref, Symbol

MAX_SOURCE_BYTES = 2_000_000
COMMENT_TYPES = frozenset({"comment", "line_comment", "block_comment", "multiline_comment"})

_parsers: dict[str, Parser] = {}
# Source of the file being parsed. Node text is always sliced from it: py-tree-sitter 0.26's
# Node.text segfaults on some inputs under Python 3.14 (seen on real Kotlin files).
_source: ContextVar[bytes] = ContextVar("crgraph_source", default=b"")


def get_parser(grammar: str) -> Parser:
    if grammar not in _parsers:
        _parsers[grammar] = tslp.get_parser(grammar)
    return _parsers[grammar]


def node_bytes(node: TSNode) -> bytes:
    return _source.get()[node.start_byte:node.end_byte]


def text(node: TSNode | None) -> str:
    return node_bytes(node).decode("utf-8", "replace") if node is not None else ""


def first_child(node: TSNode | None, type_: str) -> TSNode | None:
    if node is None:
        return None
    for child in node.named_children:
        if child.type == type_:
            return child
    return None


def children_of(node: TSNode | None, type_: str) -> list[TSNode]:
    return [c for c in node.named_children if c.type == type_] if node is not None else []


def after_token(node: TSNode, token: str) -> TSNode | None:
    """First named child after the anonymous `token` child (e.g. a property's initializer after '=')."""
    seen = False
    for child in node.children:
        if seen and child.is_named:
            return child
        seen = seen or (not child.is_named and child.type == token)
    return None


def has_token(node: TSNode, token: str) -> bool:
    """True when `node` has a direct anonymous child token such as 'interface' or 'enum'."""
    return any(not c.is_named and c.type == token for c in node.children)


def body_hash(node: TSNode) -> str:
    """Structure + token hash that ignores whitespace and comments."""
    h = hashlib.blake2b(digest_size=12)
    stack: list[tuple[TSNode, bool]] = [(node, False)]
    while stack:
        cur, closing = stack.pop()
        if closing:
            h.update(b")")
            continue
        if cur.type in COMMENT_TYPES:
            continue
        h.update(cur.type.encode())
        if cur.child_count == 0:
            h.update(b"=" + node_bytes(cur) + b";")
            continue
        h.update(b"(")
        stack.append((cur, True))
        stack.extend((c, False) for c in reversed(cur.children))
    return h.hexdigest()


def raw_hash(data: bytes) -> str:
    return hashlib.blake2b(data, digest_size=12).hexdigest()


def normalize_signature(raw: str) -> str:
    """Whitespace-insensitive form: 'fun f( a:Int ,b: X )' -> 'fun f(a: Int, b: X)'."""
    s = " ".join(raw.split())
    s = re.sub(r"([(\[<])\s+", r"\1", s)
    s = re.sub(r"\s+([)\]>,:])", r"\1", s)
    s = re.sub(r"(?<!:)([,:])(?=[^\s:])", r"\1 ", s)
    return s.rstrip(" {=:;")[:200]


def signature(node: TSNode, body: TSNode | None) -> str:
    """Declaration text up to the body, normalised, max 200 chars.

    Comments are dropped, and the text also stops at the first `{` outside parentheses/brackets, so a body
    the plugin did not name (computed property, accessor block, ...) never reaches the output.
    """
    end = body.start_byte if body is not None and body.start_byte > node.start_byte else node.end_byte
    src = _source.get()
    parts: list[bytes] = []
    pos, depth = node.start_byte, 0
    stack = [node]
    while stack:
        cur = stack.pop()
        if cur.start_byte >= end:
            break
        if cur.type in COMMENT_TYPES:
            parts.append(src[pos:cur.start_byte])
            pos = cur.end_byte
            continue
        if cur.child_count:
            stack.extend(reversed(cur.children))
            continue
        if not cur.is_named:
            if cur.type in ("(", "["):
                depth += 1
            elif cur.type in (")", "]"):
                depth -= 1
            elif cur.type == "{" and depth <= 0:
                end = cur.start_byte
                break
    parts.append(src[pos:max(pos, end)])
    return normalize_signature(b" ".join(parts).decode("utf-8", "replace"))


class LanguagePlugin(Protocol):
    name: str
    extensions: tuple[str, ...]
    call_types: frozenset[str]
    ident_types: frozenset[str]

    def grammar_for(self, path: str) -> str: ...

    def is_branch(self, node: TSNode) -> bool: ...

    def callee_name(self, node: TSNode) -> str | None: ...

    def extract(self, root: TSNode, c: Collector) -> None: ...


class Collector:
    """Accumulates FileFacts while a plugin walks a syntax tree."""

    def __init__(self, path: str, plugin: LanguagePlugin):
        self.plugin = plugin
        self.facts = FileFacts(path=path, lang=plugin.name)
        self._refs: dict[Ref, None] = {}

    def imp(self, raw: str) -> None:
        if raw and raw not in self.facts.imports:
            self.facts.imports.append(raw)

    def symbol(
        self,
        node: TSNode,
        *,
        name: str,
        kind: str,
        visibility: str,
        scope: list[str],
        body: TSNode | None = None,
        arity: int = 0,
        supertypes: list[str] | tuple[str, ...] = (),
    ) -> list[str]:
        """Record a declaration and return the scope for its members."""
        qualname = ".".join([*scope, name])
        self.facts.symbols.append(
            Symbol(
                qualname=qualname,
                name=name,
                kind=kind,
                visibility=visibility,
                signature=signature(node, body),
                body_hash=body_hash(node),
                complexity=self._complexity(body) if kind == "function" else 0,
                loc=node.end_point.row - node.start_point.row + 1,
                arity=arity,
                parent=".".join(scope) or None,
                supertypes=tuple(supertypes),
            )
        )
        for sup in supertypes:
            self.ref("inherit", sup, [*scope, name])
        return [*scope, name]

    def ref(self, kind: str, name: str | None, scope: list[str]) -> None:
        if name:
            self._refs.setdefault(Ref(kind, name, ".".join(scope) or None), None)

    def refs_in(self, node: TSNode | None, scope: list[str]) -> None:
        """Record call refs and capitalised identifier refs anywhere under `node`."""
        if node is None:
            return
        skip = getattr(self.plugin, "skip_ident_parents", frozenset())
        stack = [node]
        while stack:
            cur = stack.pop()
            if cur.type in self.plugin.call_types:
                self.ref("call", self.plugin.callee_name(cur), scope)
            elif cur.type in self.plugin.ident_types and (cur.parent is None or cur.parent.type not in skip):
                name = text(cur)
                if name[:1].isupper():
                    self.ref("type", name, scope)
            stack.extend(cur.named_children)

    def finish(self) -> FileFacts:
        self.facts.refs = list(self._refs)
        return self.facts

    def _complexity(self, body: TSNode | None) -> int:
        if body is None:
            return 1
        count, stack = 1, [body]
        while stack:
            cur = stack.pop()
            if self.plugin.is_branch(cur):
                count += 1
            stack.extend(cur.children)
        return count


def parse_source(plugin: LanguagePlugin, path: str, source: bytes) -> FileFacts:
    """Parse one file. Never raises for bad input; problems land in FileFacts.error."""
    if len(source) > MAX_SOURCE_BYTES:
        return FileFacts(path=path, lang=plugin.name, file_hash=raw_hash(source),
                         error=f"skipped: larger than {MAX_SOURCE_BYTES} bytes")
    if b"\0" in source[:8000]:
        return FileFacts(path=path, lang=plugin.name, file_hash=raw_hash(source), error="skipped: binary content")
    token = _source.set(source)
    try:
        tree = get_parser(plugin.grammar_for(path)).parse(source)
        collector = Collector(path, plugin)
        collector.facts.file_hash = body_hash(tree.root_node)
        collector.facts.loc = source.count(b"\n") + 1
        plugin.extract(tree.root_node, collector)
        return collector.finish()
    except tslp.DownloadError:
        raise
    except Exception as exc:  # one bad file must never abort the run
        return FileFacts(path=path, lang=plugin.name, file_hash=raw_hash(source), error=f"parse failed: {exc}")
    finally:
        _source.reset(token)
