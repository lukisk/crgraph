"""PHP language plugin."""
from __future__ import annotations

from tree_sitter import Node as TSNode

from .base import Collector, after_token, children_of, first_child, has_token, text

BRANCH_TYPES = frozenset({
    "if_statement", "else_if_clause", "for_statement", "foreach_statement", "while_statement", "do_statement",
    "case_statement", "catch_clause", "conditional_expression", "match_conditional_expression",
})
LOGICAL_OPERATORS = frozenset({"&&", "||", "and", "or", "??"})
TYPE_KINDS = {"class_declaration": "class", "interface_declaration": "interface", "trait_declaration": "class",
              "enum_declaration": "enum"}
NAME_TYPES = ("name", "qualified_name")


def _last(node: TSNode | None) -> str | None:
    """`Foo` or `\\App\\Foo` -> Foo."""
    if node is None:
        return None
    return text(node).rpartition("\\")[2] or None


def _visibility(node: TSNode, default: str = "public") -> str:
    word = text(first_child(node, "visibility_modifier")).lower()
    if word == "private":
        return "private"
    if word == "protected":
        return "internal"
    return default


def _arity(params: TSNode | None) -> int:
    return len(params.named_children) if params is not None else 0


class PhpPlugin:
    name = "php"
    extensions = (".php",)
    call_types = frozenset({"function_call_expression", "member_call_expression", "nullsafe_member_call_expression",
                            "scoped_call_expression", "object_creation_expression"})
    ident_types = frozenset({"name"})
    skip_ident_parents = frozenset({"namespace_name"})  # `App\Util\Helpers`: only Helpers is a type

    def grammar_for(self, path: str) -> str:
        return "php"

    def is_branch(self, node: TSNode) -> bool:
        if node.type == "binary_expression":
            return text(node.child_by_field_name("operator")).lower() in LOGICAL_OPERATORS
        return node.type in BRANCH_TYPES

    def callee_name(self, node: TSNode) -> str | None:
        if node.type == "function_call_expression":
            return _last(node.child_by_field_name("function"))
        if node.type == "object_creation_expression":  # `new Foo(...)` uses the class Foo
            return next((_last(c) for c in node.named_children if c.type in NAME_TYPES), None)
        return text(node.child_by_field_name("name")) or None

    def extract(self, root: TSNode, c: Collector) -> None:
        self._statements(root, c)

    def _statements(self, block: TSNode, c: Collector) -> None:
        for node in block.named_children:
            t = node.type
            if t == "namespace_definition":
                c.facts.package = text(node.child_by_field_name("name")) or None
                body = node.child_by_field_name("body")
                if body is not None:  # `namespace A { ... }`
                    self._statements(body, c)
            elif t == "namespace_use_declaration":
                self._use(node, c)
            elif t in TYPE_KINDS:
                self._type(node, c)
            elif t == "function_definition":
                name = text(node.child_by_field_name("name"))
                inner = c.symbol(node, name=name, kind="function", visibility="public", scope=[],
                                 body=node.child_by_field_name("body"), arity=_arity(node.child_by_field_name("parameters")))
                c.refs_in(node, inner)
            else:
                c.refs_in(node, [])

    def _use(self, node: TSNode, c: Collector) -> None:
        prefix = first_child(node, "namespace_name")  # `use A\B\{C, D as E}`
        group = node.child_by_field_name("body")
        clauses = children_of(group, "namespace_use_clause") if group is not None else children_of(node, "namespace_use_clause")
        for clause in clauses:
            target = next((ch for ch in clause.named_children if ch.type in NAME_TYPES), None)
            if target is None:
                continue
            raw = text(target).lstrip("\\")
            if prefix is not None:
                raw = f"{text(prefix).strip(chr(92))}\\{raw}"
            c.imp(raw)
            if not any(has_token(n, kw) for n in (node, clause) for kw in ("function", "const")):
                c.ref("type", raw.rpartition("\\")[2], [])

    def _type(self, node: TSNode, c: Collector) -> None:
        name = text(node.child_by_field_name("name"))
        if not name:
            return
        body = node.child_by_field_name("body")
        supers = [s for clause in ("base_clause", "class_interface_clause")
                  for s in (_last(n) for n in children_of(first_child(node, clause), "name")
                            + children_of(first_child(node, clause), "qualified_name")) if s]
        supers += [s for use in children_of(body, "use_declaration")  # traits
                   for s in (_last(n) for n in use.named_children if n.type in NAME_TYPES) if s]
        inner = c.symbol(node, name=name, kind=TYPE_KINDS[node.type], visibility="public", scope=[], body=body,
                         supertypes=supers)
        for member in body.named_children if body is not None else []:
            if member.type == "method_declaration":
                method = text(member.child_by_field_name("name"))
                scope = c.symbol(member, name=method, kind="function", visibility=_visibility(member), scope=inner,
                                 body=member.child_by_field_name("body"),
                                 arity=_arity(member.child_by_field_name("parameters")))
                c.refs_in(member, scope)
            elif member.type == "property_declaration":
                elements = children_of(member, "property_element")
                for element in elements:
                    prop = text(first_child(element.child_by_field_name("name"), "name"))
                    if prop:  # a single property keeps its modifiers and type in the signature
                        scope = c.symbol(member if len(elements) == 1 else element, name=prop, kind="property",
                                         visibility=_visibility(member), scope=inner,
                                         body=element.child_by_field_name("default_value"))
                        c.refs_in(member, scope)
            elif member.type == "const_declaration":
                for element in children_of(member, "const_element"):
                    const = text(first_child(element, "name"))
                    if const:  # the value is not part of the signature
                        c.symbol(element, name=const, kind="property", visibility=_visibility(member), scope=inner,
                                 body=after_token(element, "="))
            elif member.type != "use_declaration":
                c.refs_in(member, inner)
