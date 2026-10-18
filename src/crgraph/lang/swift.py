"""Swift language plugin."""
from __future__ import annotations

from tree_sitter import Node as TSNode

from .base import Collector, after_token, children_of, first_child, text

BRANCH_TYPES = frozenset({
    "if_statement", "guard_statement", "switch_entry", "for_statement", "while_statement",
    "repeat_while_statement", "catch_block", "ternary_expression", "conjunction_expression",
    "disjunction_expression", "nil_coalescing_expression",
})
KIND_BY_KEYWORD = {"class": "class", "struct": "class", "actor": "class", "enum": "enum", "extension": "extension"}


def _visibility(node: TSNode, default: str = "internal") -> str:
    """Explicit access level, else `default`: Swift's internal, or the access level of the enclosing extension."""
    word = text(first_child(first_child(node, "modifiers"), "visibility_modifier"))
    if word in ("public", "open"):
        return "public"
    if word in ("private", "fileprivate"):
        return "private"
    return default


def _type_name(node: TSNode | None) -> str:
    if node is None:
        return ""
    if node.type == "user_type":
        names = children_of(node, "type_identifier")
        return text(names[-1]) if names else text(node)
    return text(node)


class SwiftPlugin:
    name = "swift"
    extensions = (".swift",)
    call_types = frozenset({"call_expression"})
    ident_types = frozenset({"simple_identifier", "type_identifier"})

    def grammar_for(self, path: str) -> str:
        return "swift"

    def is_branch(self, node: TSNode) -> bool:
        return node.type in BRANCH_TYPES

    def callee_name(self, node: TSNode) -> str | None:
        first = node.named_children[0] if node.named_children else None
        if first is None:
            return None
        if first.type == "simple_identifier":
            return text(first)
        if first.type == "navigation_expression":
            suffix = first.child_by_field_name("suffix")
            ident = suffix.child_by_field_name("suffix") if suffix is not None else None
            return text(ident) or None
        return None

    def extract(self, root: TSNode, c: Collector) -> None:
        for child in root.named_children:
            if child.type == "import_declaration":
                ident = first_child(child, "identifier")
                if ident is not None:
                    c.imp(text(ident))
            else:
                self._decl(child, c, [])

    def _decl(self, node: TSNode, c: Collector, scope: list[str], default_vis: str = "internal") -> None:
        t = node.type
        if t in ("class_declaration", "protocol_declaration"):
            name = _type_name(node.child_by_field_name("name"))
            if not name:
                return
            if t == "protocol_declaration":
                kind = "interface"
            else:
                keyword = node.child_by_field_name("declaration_kind")
                kind = KIND_BY_KEYWORD.get(keyword.type if keyword is not None else "class", "class")
            supers = [_type_name(s.child_by_field_name("inherits_from"))
                      for s in children_of(node, "inheritance_specifier")]
            body = node.child_by_field_name("body")
            visibility = _visibility(node, default_vis)
            inner = c.symbol(node, name=name, kind=kind, visibility=visibility, scope=scope,
                             body=body, supertypes=[s for s in supers if s])
            member_vis = visibility if kind == "extension" else "internal"  # `private extension X { func f() }`
            for member in body.named_children if body is not None else []:
                self._decl(member, c, inner, member_vis)
        elif t in ("function_declaration", "protocol_function_declaration", "init_declaration"):
            name = "init" if t == "init_declaration" else text(node.child_by_field_name("name"))
            if not name:
                return
            inner = c.symbol(node, name=name, kind="function", visibility=_visibility(node, default_vis), scope=scope,
                             body=node.child_by_field_name("body"), arity=len(children_of(node, "parameter")))
            c.refs_in(node, inner)
        elif t in ("property_declaration", "protocol_property_declaration"):
            pattern = node.child_by_field_name("name")
            ident = pattern.child_by_field_name("bound_identifier") if pattern is not None else None
            if ident is None:
                return
            inner = c.symbol(node, name=text(ident), kind="property", visibility=_visibility(node, default_vis),
                             scope=scope,
                             body=node.child_by_field_name("value") or after_token(node, "="))
            c.refs_in(node, inner)
        elif t != "enum_entry":
            c.refs_in(node, scope)
