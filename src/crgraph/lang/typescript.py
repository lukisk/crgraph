"""TypeScript / JavaScript (incl. TSX/JSX) language plugin."""
from __future__ import annotations

from pathlib import PurePosixPath

from tree_sitter import Node as TSNode

from .base import Collector, children_of, first_child, text

BRANCH_TYPES = frozenset({
    "if_statement", "for_statement", "for_in_statement", "while_statement", "do_statement",
    "switch_case", "catch_clause", "ternary_expression",
})
LOGICAL_OPERATORS = frozenset({"&&", "||", "??"})
FUNCTION_VALUES = frozenset({"arrow_function", "function_expression", "function", "generator_function"})
GRAMMAR_BY_SUFFIX = {".ts": "typescript", ".mts": "typescript", ".cts": "typescript", ".tsx": "tsx"}


def _string(node: TSNode | None) -> str:
    fragment = first_child(node, "string_fragment")
    return text(fragment) if fragment is not None else text(node).strip("'\"`")


def _require_target(value: TSNode | None) -> str | None:
    if value is not None and value.type == "call_expression" and text(value.child_by_field_name("function")) == "require":
        args = value.child_by_field_name("arguments")
        first = args.named_children[0] if args is not None and args.named_children else None
        if first is not None and first.type == "string":
            return _string(first)
    return None


class TypeScriptPlugin:
    name = "typescript"
    extensions = (".ts", ".tsx", ".mts", ".cts", ".js", ".jsx", ".mjs", ".cjs")
    call_types = frozenset({"call_expression", "new_expression"})
    ident_types = frozenset({"identifier", "type_identifier"})

    def grammar_for(self, path: str) -> str:
        return GRAMMAR_BY_SUFFIX.get(PurePosixPath(path).suffix, "javascript")

    def is_branch(self, node: TSNode) -> bool:
        if node.type == "binary_expression":
            return text(node.child_by_field_name("operator")) in LOGICAL_OPERATORS
        return node.type in BRANCH_TYPES

    def callee_name(self, node: TSNode) -> str | None:
        fn = node.child_by_field_name("function") or node.child_by_field_name("constructor")
        if fn is None:
            return None
        if fn.type == "identifier":
            return text(fn)
        if fn.type == "member_expression":
            return text(fn.child_by_field_name("property")) or None
        return None

    def extract(self, root: TSNode, c: Collector) -> None:
        for child in root.named_children:
            self._top(child, c, exported=False)

    def _top(self, node: TSNode, c: Collector, exported: bool) -> None:
        t = node.type
        vis = "public" if exported else "internal"
        if t == "import_statement":
            c.imp(_string(node.child_by_field_name("source")))
            clause = first_child(node, "import_clause")
            stack = [clause] if clause is not None else []
            while stack:
                cur = stack.pop()
                if cur.type == "identifier":
                    c.ref("type", text(cur), [])
                elif cur.type == "import_specifier":
                    c.ref("type", text(cur.child_by_field_name("name")), [])
                else:
                    stack.extend(cur.named_children)
        elif t == "export_statement":
            source = node.child_by_field_name("source")
            declaration = node.child_by_field_name("declaration")
            if source is not None:
                c.imp(_string(source))
            elif declaration is not None:
                self._top(declaration, c, exported=True)
            else:
                c.refs_in(node, [])
        elif t in ("class_declaration", "abstract_class_declaration", "class"):
            self._class(node, c, vis)
        elif t in ("interface_declaration", "type_alias_declaration", "enum_declaration"):
            name = text(node.child_by_field_name("name"))
            kind = "enum" if t == "enum_declaration" else "interface"
            supers = [text(n) for n in children_of(first_child(node, "extends_type_clause"), "type_identifier")]
            inner = c.symbol(node, name=name, kind=kind, visibility=vis, scope=[],
                             body=node.child_by_field_name("body") or node.child_by_field_name("value"),
                             supertypes=supers)
            c.refs_in(node.child_by_field_name("body") or node.child_by_field_name("value"), inner)
        elif t in ("function_declaration", "generator_function_declaration"):
            name = text(node.child_by_field_name("name"))
            params = node.child_by_field_name("parameters")
            inner = c.symbol(node, name=name, kind="function", visibility=vis, scope=[],
                             body=node.child_by_field_name("body"),
                             arity=len(params.named_children) if params is not None else 0)
            c.refs_in(node, inner)
        elif t in ("lexical_declaration", "variable_declaration"):
            for decl in children_of(node, "variable_declarator"):
                self._variable(decl, c, vis)
        else:
            c.refs_in(node, [])

    def _variable(self, decl: TSNode, c: Collector, vis: str) -> None:
        name_node = decl.child_by_field_name("name")
        value = decl.child_by_field_name("value")
        target = _require_target(value)
        if target:
            c.imp(target)
        if name_node is None or name_node.type != "identifier":
            c.refs_in(value, [])
            return
        if value is not None and value.type in FUNCTION_VALUES:
            params = value.child_by_field_name("parameters")
            arity = len(params.named_children) if params is not None else (1 if value.child_by_field_name("parameter") else 0)
            inner = c.symbol(decl, name=text(name_node), kind="function", visibility=vis, scope=[],
                             body=value.child_by_field_name("body"), arity=arity)
        else:
            inner = c.symbol(decl, name=text(name_node), kind="property", visibility=vis, scope=[], body=value)
        c.refs_in(value, inner)

    def _class(self, node: TSNode, c: Collector, vis: str) -> None:
        name = text(node.child_by_field_name("name"))
        if not name:
            return
        heritage = first_child(node, "class_heritage")
        supers: list[str] = []
        for clause in heritage.named_children if heritage is not None else []:
            for item in clause.named_children:
                if item.type in ("identifier", "type_identifier"):
                    supers.append(text(item))
                elif item.type in ("member_expression", "generic_type"):
                    supers.append(text(item).split("<")[0].split(".")[-1])
        body = node.child_by_field_name("body")
        inner = c.symbol(node, name=name, kind="class", visibility=vis, scope=[], body=body, supertypes=supers)
        for member in body.named_children if body is not None else []:
            member_name = text(member.child_by_field_name("name"))
            accessibility = text(first_child(member, "accessibility_modifier"))
            member_vis = "private" if accessibility in ("private", "protected") or member_name.startswith("#") else vis
            if member.type in ("method_definition", "method_signature", "abstract_method_signature"):
                params = member.child_by_field_name("parameters")
                scope = c.symbol(member, name=member_name, kind="function", visibility=member_vis, scope=inner,
                                 body=member.child_by_field_name("body"),
                                 arity=len(params.named_children) if params is not None else 0)
                c.refs_in(member, scope)
            elif member.type in ("public_field_definition", "field_definition"):
                scope = c.symbol(member, name=member_name, kind="property", visibility=member_vis, scope=inner,
                                 body=member.child_by_field_name("value"))
                c.refs_in(member.child_by_field_name("value"), scope)
            else:
                c.refs_in(member, inner)
