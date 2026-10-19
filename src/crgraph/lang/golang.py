"""Go language plugin."""
from __future__ import annotations

import posixpath

from tree_sitter import Node as TSNode

from .base import Collector, children_of, first_child, text

BRANCH_TYPES = frozenset({
    "if_statement", "for_statement", "expression_case", "type_case", "communication_case",
})
LOGICAL_OPERATORS = frozenset({"&&", "||"})
TYPE_KINDS = {"struct_type": "class", "interface_type": "interface"}


def _visibility(name: str) -> str:
    """Exported (capitalised) names are public; the rest is visible in the whole package, never file-private."""
    return "public" if name[:1].isupper() else "internal"


def _names(node: TSNode) -> list[TSNode]:
    """`a, b int`: the grammar also files the commas under "name"."""
    return [n for n in node.children_by_field_name("name") if n.is_named]


def _arity(params: TSNode | None) -> int:
    count = 0
    for p in params.named_children if params is not None else []:
        if p.type in ("parameter_declaration", "variadic_parameter_declaration"):
            count += max(1, len(_names(p)))
    return count


def _type_name(node: TSNode | None) -> str | None:
    """`T`, `*T`, `pkg.T` or `T[K]` -> T."""
    while node is not None and node.type in ("pointer_type", "generic_type", "parenthesized_type"):
        node = node.child_by_field_name("type") or (node.named_children[0] if node.named_children else None)
    if node is None:
        return None
    if node.type == "qualified_type":
        return text(node.child_by_field_name("name")) or None
    return text(node) if node.type == "type_identifier" else None


class GoPlugin:
    name = "go"
    extensions = (".go",)
    call_types = frozenset({"call_expression"})
    ident_types = frozenset({"type_identifier"})

    def grammar_for(self, path: str) -> str:
        return "go"

    def is_branch(self, node: TSNode) -> bool:
        if node.type == "binary_expression":
            return text(node.child_by_field_name("operator")) in LOGICAL_OPERATORS
        return node.type in BRANCH_TYPES

    def callee_name(self, node: TSNode) -> str | None:
        fn = node.child_by_field_name("function")
        if fn is None:
            return None
        if fn.type == "identifier":
            return text(fn)
        if fn.type == "selector_expression":
            return text(fn.child_by_field_name("field")) or None
        return None

    def extract(self, root: TSNode, c: Collector) -> None:
        c.facts.package = posixpath.dirname(c.facts.path)  # a Go package is a directory
        for child in root.named_children:
            t = child.type
            if t == "import_declaration":
                for spec in [*children_of(child, "import_spec"), *children_of(first_child(child, "import_spec_list"), "import_spec")]:
                    c.imp(text(spec.child_by_field_name("path")).strip('"`'))
            elif t == "type_declaration":
                for spec in [*children_of(child, "type_spec"), *children_of(child, "type_alias")]:
                    self._type(spec, c)
            elif t == "function_declaration":
                name = text(child.child_by_field_name("name"))
                inner = c.symbol(child, name=name, kind="function", visibility=_visibility(name), scope=[],
                                 body=child.child_by_field_name("body"),
                                 arity=_arity(child.child_by_field_name("parameters")))
                c.refs_in(child, inner)
            elif t == "method_declaration":
                receiver = child.child_by_field_name("receiver")
                decl = first_child(receiver, "parameter_declaration")
                owner = _type_name(decl.child_by_field_name("type")) if decl is not None else None
                name = text(child.child_by_field_name("name"))
                inner = c.symbol(child, name=name, kind="function", visibility=_visibility(name),
                                 scope=[owner] if owner else [], body=child.child_by_field_name("body"),
                                 arity=_arity(child.child_by_field_name("parameters")))
                c.refs_in(child, inner)
            elif t in ("const_declaration", "var_declaration"):
                for spec in [*children_of(child, "const_spec"), *children_of(child, "var_spec"),
                             *children_of(first_child(child, "var_spec_list"), "var_spec")]:
                    for name_node in _names(spec):
                        name = text(name_node)
                        if name == "_":
                            continue
                        inner = c.symbol(spec, name=name, kind="property", visibility=_visibility(name), scope=[],
                                         body=spec.child_by_field_name("value"))
                        c.refs_in(spec, inner)
            else:
                c.refs_in(child, [])

    def _type(self, spec: TSNode, c: Collector) -> None:
        name = text(spec.child_by_field_name("name"))
        type_node = spec.child_by_field_name("type")
        kind = TYPE_KINDS.get(type_node.type if type_node is not None else "", "class")
        supers: list[str] = []  # embedded structs and interfaces
        if type_node is not None and type_node.type == "struct_type":
            for field in children_of(first_child(type_node, "field_declaration_list"), "field_declaration"):
                if field.child_by_field_name("name") is None:
                    supers += [s for s in [_type_name(field.child_by_field_name("type"))] if s]
        elif type_node is not None and type_node.type == "interface_type":
            for elem in children_of(type_node, "type_elem"):
                supers += [s for s in (_type_name(t) for t in elem.named_children) if s]
        # no body: the signature runs to the first `{`, so it reads "Client struct" / "Payer interface"
        inner = c.symbol(spec, name=name, kind=kind, visibility=_visibility(name), scope=[], supertypes=supers)
        if type_node is not None and type_node.type == "interface_type":
            for elem in children_of(type_node, "method_elem"):
                method = text(elem.child_by_field_name("name"))
                scope = c.symbol(elem, name=method, kind="function", visibility=_visibility(method), scope=inner,
                                 arity=_arity(elem.child_by_field_name("parameters")))
                c.refs_in(elem, scope)
        c.refs_in(type_node, inner)
