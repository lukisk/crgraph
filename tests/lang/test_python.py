from textwrap import dedent

from crgraph.lang import parse_source, plugin_for
from crgraph.model import Ref

SRC = '''
import os, sys as s
from .a import b, c as d
from ..pkg.mod import *

class Foo(Base, mixins.Mixin):
    def load(self, id: int) -> "Product":
        if id and x:
            return self.r.get(id)
        return y()

def _priv():
    pass

async def top(a, b=1):
    Foo().load(1)

__all__ = ["Foo"]
'''


def parse(path: str, src: str):
    return parse_source(plugin_for(path), path, dedent(src).encode())


def test_symbols_visibility_and_metrics():
    facts = parse("pkg/m.py", SRC)
    by = {s.qualname: s for s in facts.symbols}
    assert set(by) == {"Foo", "Foo.load", "_priv", "top"}
    assert by["Foo"].kind == "class"
    assert by["Foo"].supertypes == ("Base", "Mixin")
    assert by["Foo.load"].arity == 1
    assert by["Foo.load"].parent == "Foo"
    assert by["Foo.load"].complexity == 3
    assert by["Foo.load"].signature == 'def load(self, id: int) -> "Product"'
    assert by["Foo"].visibility == "public"
    assert by["_priv"].visibility == "private"
    assert by["top"].visibility == "internal"  # not listed in __all__
    assert by["top"].arity == 2


def test_imports_include_relative_and_member_forms():
    facts = parse("pkg/m.py", SRC)
    assert facts.imports == ["os", "sys", ".a", ".a.b", ".a.c", "..pkg.mod"]


def test_refs():
    facts = parse("pkg/m.py", SRC)
    assert Ref("call", "load", "top") in facts.refs
    assert Ref("call", "Foo", "top") in facts.refs
    assert Ref("inherit", "Base", "Foo") in facts.refs


def test_whitespace_and_comments_do_not_change_hashes():
    a = parse("m.py", "def f(x):\n    return x + 1\n")
    b = parse("m.py", "def f( x ):\n    # note\n    return x+1   # trailing\n")
    assert a.file_hash == b.file_hash
    assert a.symbols[0].body_hash == b.symbols[0].body_hash


def test_indentation_change_changes_hash():
    a = parse("m.py", "def f(x):\n    if x:\n        g()\n        h()\n")
    b = parse("m.py", "def f(x):\n    if x:\n        g()\n    h()\n")
    assert a.symbols[0].body_hash != b.symbols[0].body_hash


def test_binary_and_oversized_sources_are_skipped():
    binary = parse_source(plugin_for("a.py"), "a.py", b"\0\1\2")
    assert binary.error.startswith("skipped") and binary.symbols == []
    big = parse_source(plugin_for("a.py"), "a.py", b"x = 1\n" * 400_000)
    assert big.error.startswith("skipped")


def test_broken_syntax_still_extracts():
    facts = parse("m.py", "def ok():\n    pass\n\ndef broken(:\n")
    assert "ok" in {s.qualname for s in facts.symbols}
    assert facts.error is None


def test_build_files_and_unknown_files_have_no_plugin():
    assert plugin_for("a/build.gradle.kts") is None
    assert plugin_for("x.txt") is None
    assert plugin_for("types/index.d.ts") is None


def test_signature_normalisation():
    from crgraph.lang.base import normalize_signature

    assert normalize_signature("fun f( a:Int ,b: X )") == "fun f(a: Int, b: X)"
    assert normalize_signature("fun load( id: Int ): Flow< Product >") == "fun load(id: Int): Flow<Product>"
    assert normalize_signature("public func load(id: Int) -> Product {") == "public func load(id: Int) -> Product"
    assert normalize_signature("val x = A::b") == "val x = A::b"
