from textwrap import dedent

from crgraph.lang import parse_source, plugin_for
from crgraph.model import Ref

SRC = '''
import { Repo, type Product } from "./repo";
import * as util from "../util/index";
import React from "react";
const legacy = require("./legacy");
export { helper } from "./helpers";

export interface Loader { load(id: number): Product }
export type Id = string | number;
export enum Mode { A, B }

export class ProductStore extends BaseStore implements Loader {
  private cache = new Map<string, Product>();
  load(id: number): Product {
    if (id > 0 && this.ready) { return this.repo.fetch(id) ?? fallback(); }
    return Repo.empty();
  }
  private reset(): void {}
}

export function useProduct(id: number) { return new ProductStore().load(id); }
export const format = (m: Product, short: boolean) => (short ? m.id : `${m.id}`);
function internalHelper() {}
'''


def parse(path: str, src: str):
    return parse_source(plugin_for(path), path, dedent(src).encode())


def test_imports():
    assert parse("web/src/store.ts", SRC).imports == ["./repo", "../util/index", "react", "./legacy", "./helpers"]


def test_declarations():
    by = {s.qualname: s for s in parse("web/src/store.ts", SRC).symbols}
    assert set(by) == {
        "legacy", "Loader", "Id", "Mode", "ProductStore", "ProductStore.cache", "ProductStore.load",
        "ProductStore.reset", "useProduct", "format", "internalHelper",
    }
    assert by["Loader"].kind == "interface" and by["Id"].kind == "interface"
    assert by["Mode"].kind == "enum"
    assert by["ProductStore"].supertypes == ("BaseStore", "Loader")
    assert by["ProductStore"].visibility == "public"
    assert by["ProductStore.cache"].visibility == "private"
    assert by["ProductStore.reset"].visibility == "private"
    assert by["ProductStore.load"].visibility == "public"
    assert by["ProductStore.load"].complexity == 4
    assert by["format"].kind == "function" and by["format"].arity == 2
    assert by["format"].complexity == 2
    assert by["internalHelper"].visibility == "internal"
    assert by["legacy"].kind == "property"


def test_refs():
    refs = set(parse("web/src/store.ts", SRC).refs)
    assert Ref("call", "fetch", "ProductStore.load") in refs
    assert Ref("call", "ProductStore", "useProduct") in refs
    assert Ref("type", "Repo", None) in refs  # imported name, file level
    assert Ref("inherit", "BaseStore", "ProductStore") in refs


def test_tsx_component_references():
    facts = parse("web/src/App.tsx", 'export function App() { return <Header title="x" />; }\n')
    assert Ref("type", "Header", "App") in set(facts.refs)


def test_javascript_uses_javascript_grammar():
    facts = parse("tools/q.mjs", "export function run(a) { return a ? 1 : 2 }\n")
    assert [s.qualname for s in facts.symbols] == ["run"]
