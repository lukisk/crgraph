from textwrap import dedent

from crgraph.lang import parse_source, plugin_for
from crgraph.model import Ref

SRC = '''
package com.example.app.detail

import com.example.app.core.Repo
import com.example.app.util.*
import com.example.app.model.Product as M

interface Loader { fun load(id: Int): M }

enum class Mode { A, B }

object Registry {
    private val items = mutableListOf<String>()
}

internal class DetailViewModel(private val repo: Repo) : BaseViewModel(), Loader {
    companion object { const val KEY = "k" }

    override fun load(id: Int): M {
        if (id > 0 && enabled) return repo.fetch(id) ?: fallback()
        return Repo.empty()
    }

    protected fun String.shout(a: Int, b: Int) {}
}

private fun helper() = Registry.toString()
'''


def facts():
    return parse_source(plugin_for("app/src/main/kotlin/D.kt"), "app/src/main/kotlin/D.kt", dedent(SRC).encode())


def test_package_and_imports():
    f = facts()
    assert f.package == "com.example.app.detail"
    assert f.imports == ["com.example.app.core.Repo", "com.example.app.util.*", "com.example.app.model.Product"]


def test_declarations():
    by = {s.qualname: s for s in facts().symbols}
    assert set(by) == {
        "Loader", "Loader.load", "Mode", "Registry", "Registry.items", "DetailViewModel",
        "DetailViewModel.Companion", "DetailViewModel.Companion.KEY", "DetailViewModel.load",
        "DetailViewModel.shout", "helper",
    }
    assert by["Loader"].kind == "interface"
    assert by["Mode"].kind == "enum"
    assert by["Registry"].kind == "object"
    assert by["DetailViewModel.Companion"].kind == "object"
    assert by["DetailViewModel"].kind == "class"
    assert by["Registry.items"].kind == "property"
    assert by["DetailViewModel"].supertypes == ("BaseViewModel", "Loader")


def test_visibility_arity_complexity_signature():
    by = {s.qualname: s for s in facts().symbols}
    assert by["DetailViewModel"].visibility == "internal"
    assert by["Registry.items"].visibility == "private"
    assert by["DetailViewModel.shout"].visibility == "internal"
    assert by["helper"].visibility == "private"
    assert by["Loader"].visibility == "public"
    assert by["DetailViewModel.shout"].arity == 2
    assert by["DetailViewModel.load"].complexity == 4
    assert by["DetailViewModel.load"].signature == "override fun load(id: Int): M"


def test_refs():
    refs = set(facts().refs)
    assert Ref("call", "fetch", "DetailViewModel.load") in refs
    assert Ref("call", "empty", "DetailViewModel.load") in refs
    assert Ref("type", "Repo", "DetailViewModel.load") in refs
    assert Ref("type", "Repo", "DetailViewModel") in refs  # constructor parameter type
    assert Ref("inherit", "Loader", "DetailViewModel") in refs
    assert Ref("call", "toString", "helper") in refs
