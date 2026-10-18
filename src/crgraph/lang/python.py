"""Python language plugin."""
from __future__ import annotations

from tree_sitter import Node as TSNode

from .base import Collector, text

BRANCH_TYPES = frozenset({
    "if_statement", "elif_clause", "for_statement", "while_statement", "except_clause",
    "conditional_expression", "boolean_operator", "case_clause", "for_in_clause", "if_clause",
})


def _visibility(name: str, scope: list[str], exported: set[str] | None) -> str:
    if name.startswith("_") and not (name.startswith("__") and name.endswith("__")):
        return "private"
    if not scope and exported is not None and name not in exported:
        return "internal"
    return "public"


def _dunder_all(root: TSNode) -> set[str] | None:
    for child in root.named_children:
        node = child.named_children[0] if child.type == "expression_statement" and child.named_children else child
        if node.type == "assignment" and text(node.child_by_field_name("left")) == "__all__":
            right = node.child_by_field_name("right")
            if right is not None and right.type in ("list", "tuple"):
                return {text(s).strip("\"'") for s in right.named_children if s.type == "string"}
    return None


class PythonPlugin:
    name = "python"
    extensions = (".py",)
    call_types = frozenset({"call"})
    ident_types = frozenset({"identifier"})

    def grammar_for(self, path: str) -> str:
        return "python"

    def is_branch(self, node: TSNode) -> bool:
        return node.type in BRANCH_TYPES

    def callee_name(self, node: TSNode) -> str | None:
        fn = node.child_by_field_name("function")
        if fn is None:
            return None
        if fn.type == "identifier":
            return text(fn)
        if fn.type == "attribute":
            return text(fn.child_by_field_name("attribute"))
        return None

    def extract(self, root: TSNode, c: Collector) -> None:
        self._block(root, c, [], _dunder_all(root))

    def _block(self, block: TSNode, c: Collector, scope: list[str], exported: set[str] | None) -> None:
        for child in block.named_children:
            node = child.child_by_field_name("definition") if child.type == "decorated_definition" else child
            if node is None:
                continue
            if node.type == "import_statement":
                for name in node.children_by_field_name("name"):
                    target = name.child_by_field_name("name") if name.type == "aliased_import" else name
                    c.imp(text(target))
            elif node.type == "import_from_statement":
                base = text(node.child_by_field_name("module_name"))
                c.imp(base)
                for name in node.children_by_field_name("name"):
                    target = name.child_by_field_name("name") if name.type == "aliased_import" else name
                    member = text(target)
                    c.imp(base + member if base.endswith(".") else f"{base}.{member}")
                    c.ref("type", member.split(".")[-1], scope)
            elif node.type == "class_definition":
                name = text(node.child_by_field_name("name"))
                supers = node.child_by_field_name("superclasses")
                bases = [text(a).split(".")[-1] for a in (supers.named_children if supers else [])
                         if a.type in ("identifier", "attribute")]
                body = node.child_by_field_name("body")
                inner = c.symbol(node, name=name, kind="class", visibility=_visibility(name, scope, exported),
                                 scope=scope, body=body, supertypes=bases)
                if body is not None:
                    self._block(body, c, inner, exported)
            elif node.type == "function_definition":
                name = text(node.child_by_field_name("name"))
                params = node.child_by_field_name("parameters")
                arity = sum(1 for p in (params.named_children if params else []) if text(p) not in ("self", "cls"))
                inner = c.symbol(node, name=name, kind="function", visibility=_visibility(name, scope, exported),
                                 scope=scope, body=node.child_by_field_name("body"), arity=arity)
                c.refs_in(node, inner)
            else:
                c.refs_in(node, scope)
