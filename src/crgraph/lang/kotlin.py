"""Kotlin language plugin (the tree-sitter Kotlin grammar has no field names, so we walk by node type)."""
from __future__ import annotations

from tree_sitter import Node as TSNode

from .base import Collector, after_token, children_of, first_child, has_token, text

BRANCH_TYPES = frozenset({
    "if_expression", "when_entry", "for_statement", "while_statement", "do_while_statement",
    "catch_block", "elvis_expression", "conjunction_expression", "disjunction_expression",
})


def _visibility(node: TSNode) -> str:
    vis = first_child(first_child(node, "modifiers"), "visibility_modifier")
    word = text(vis)
    if word == "private":
        return "private"
    if word in ("internal", "protected"):
        return "internal"
    return "public"


def _last_type_name(node: TSNode) -> str | None:
    """Last type_identifier of the first user_type under `node` (handles qualified a.b.C)."""
    stack = [node]
    while stack:
        cur = stack.pop(0)
        if cur.type == "user_type":
            names = children_of(cur, "type_identifier")
            return text(names[-1]) if names else None
        stack.extend(cur.named_children)
    return None


class KotlinPlugin:
    name = "kotlin"
    extensions = (".kt", ".kts")
    call_types = frozenset({"call_expression"})
    ident_types = frozenset({"simple_identifier", "type_identifier"})

    def grammar_for(self, path: str) -> str:
        return "kotlin"

    def is_branch(self, node: TSNode) -> bool:
        return node.type in BRANCH_TYPES

    def callee_name(self, node: TSNode) -> str | None:
        first = node.named_children[0] if node.named_children else None
        if first is None:
            return None
        if first.type == "simple_identifier":
            return text(first)
        if first.type == "navigation_expression" and first.named_children:
            ids = children_of(first.named_children[-1], "simple_identifier")
            return text(ids[-1]) if ids else None
        return None

    def extract(self, root: TSNode, c: Collector) -> None:
        for child in root.named_children:
            if child.type == "package_header":
                c.facts.package = text(first_child(child, "identifier")) or None
            elif child.type == "import_list":
                for header in children_of(child, "import_header"):
                    self._import(header, c)
            elif child.type == "import_header":
                self._import(child, c)
            else:
                self._decl(child, c, [])

    def _import(self, header: TSNode, c: Collector) -> None:
        ident = first_child(header, "identifier")
        if ident is not None:
            c.imp(text(ident) + (".*" if first_child(header, "wildcard_import") else ""))

    def _decl(self, node: TSNode, c: Collector, scope: list[str]) -> None:
        t = node.type
        if t in ("class_declaration", "object_declaration", "companion_object"):
            name_node = first_child(node, "type_identifier")
            name = text(name_node) if name_node else ("Companion" if t == "companion_object" else "")
            if not name:
                return
            if t != "class_declaration":
                kind = "object"
            elif has_token(node, "interface"):
                kind = "interface"
            elif has_token(node, "enum") or first_child(node, "enum_class_body"):
                kind = "enum"
            else:
                kind = "class"
            body = first_child(node, "class_body") or first_child(node, "enum_class_body")
            supers = [s for s in (_last_type_name(d) for d in children_of(node, "delegation_specifier")) if s]
            inner = c.symbol(node, name=name, kind=kind, visibility=_visibility(node), scope=scope,
                             body=body, supertypes=supers)
            c.refs_in(first_child(node, "primary_constructor"), inner)
            for member in body.named_children if body is not None else []:
                self._decl(member, c, inner)
        elif t == "function_declaration":
            name_node = first_child(node, "simple_identifier")
            if name_node is None:
                return
            params = first_child(node, "function_value_parameters")
            inner = c.symbol(node, name=text(name_node), kind="function", visibility=_visibility(node),
                             scope=scope, body=first_child(node, "function_body"),
                             arity=len(children_of(params, "parameter")))
            c.refs_in(node, inner)
        elif t == "property_declaration":
            name_node = first_child(first_child(node, "variable_declaration"), "simple_identifier")
            if name_node is None:
                return
            initializer = (after_token(node, "=") or first_child(node, "property_delegate")
                           or first_child(node, "getter") or first_child(node, "setter"))
            inner = c.symbol(node, name=text(name_node), kind="property", visibility=_visibility(node), scope=scope,
                             body=initializer)
            c.refs_in(node, inner)
        elif t != "enum_entry":
            c.refs_in(node, scope)
