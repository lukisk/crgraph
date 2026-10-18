from textwrap import dedent

from crgraph.lang import parse_source, plugin_for
from crgraph.model import Ref

SRC = '''
import Foundation
import ShopCore

public protocol ProductLoading {
    func load(id: Int) -> Product
}

public final class ProductRepository: BaseRepository, ProductLoading {
    private let client: Client
    public init(client: Client) { self.client = client }
    public func load(id: Int) -> Product {
        guard id > 0 else { return Product.empty }
        return client.fetch(id: id) ?? fallback()
    }
}

extension ProductRepository {
    func refresh() { load(id: 1) }
}

struct Helper {}
'''


def facts():
    path = "ios/Frameworks/ShopProducts/Sources/ProductRepository.swift"
    return parse_source(plugin_for(path), path, dedent(SRC).encode())


def test_imports():
    assert facts().imports == ["Foundation", "ShopCore"]


def test_declarations():
    symbols = facts().symbols
    kinds = {(s.qualname, s.kind) for s in symbols}
    assert ("ProductLoading", "interface") in kinds
    assert ("ProductLoading.load", "function") in kinds
    assert ("ProductRepository", "class") in kinds
    assert ("ProductRepository", "extension") in kinds
    assert ("ProductRepository.client", "property") in kinds
    assert ("ProductRepository.init", "function") in kinds
    assert ("ProductRepository.refresh", "function") in kinds
    assert ("Helper", "class") in kinds
    by = {s.qualname: s for s in symbols if s.kind != "extension"}
    assert by["ProductRepository"].supertypes == ("BaseRepository", "ProductLoading")
    assert by["ProductRepository"].visibility == "public"
    assert by["ProductRepository.client"].visibility == "private"
    assert by["ProductRepository.refresh"].visibility == "internal"
    assert by["Helper"].visibility == "internal"
    assert by["ProductRepository.init"].arity == 1
    assert by["ProductRepository.load"].complexity == 3
    assert by["ProductRepository.load"].signature == "public func load(id: Int) -> Product"


def test_refs():
    refs = set(facts().refs)
    assert Ref("call", "load", "ProductRepository.refresh") in refs
    assert Ref("call", "fetch", "ProductRepository.load") in refs
    assert Ref("inherit", "BaseRepository", "ProductRepository") in refs


def test_members_of_an_extension_take_its_access_level_by_default():
    src = ("private extension A {\n    func p() {}\n    public func q() {}\n}\n"
           "public extension A {\n    func r() {}\n}\nextension A {\n    func s() {}\n}\n")
    facts = parse_source(plugin_for("a.swift"), "a.swift", src.encode())
    vis = {s.qualname: s.visibility for s in facts.symbols if s.kind == "function"}
    assert vis == {"A.p": "private", "A.q": "public", "A.r": "public", "A.s": "internal"}
