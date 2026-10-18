import pytest

from crgraph.lang import parse_source, plugin_for

# Every BODY_* / COMMENT_* token sits in a body, initializer, accessor or comment: none may reach a signature.
CASES = {
    "a.kt": """
class A {
  val g: String
    get() = "BODY_GETTER"
  var x: Int = 0
    set(value) { field = value + BODY_SETTER }
  fun f(a: Int, /* COMMENT_PARAM */ b: Int) // COMMENT_TRAILING
  { body() }
  val lazyV by lazy { "BODY_LAZY" }
}
data class State(
    // COMMENT_CTOR
    val a: Int,
)
val top: String get() { return "BODY_TOP_GETTER" }
""",
    "a.swift": """
public struct S {
  public var body: some View { VStack { Text("BODY_COMPUTED") } }
  var observed: Int = 0 { didSet { BODY_DIDSET() } }
  var onlyObserved: Int { willSet { BODY_WILLSET() } }
  public func g() -> Int { return 1 } // COMMENT_TRAILING
}
""",
    "a.py": """
def f(a, b):  # COMMENT_DEF
    return BODY_PY
class C:  # COMMENT_CLASS
    x = 1
""",
    "a.ts": """
export const CONFIG = { apiKey: "BODY_TS_INIT" };
export class K { handler = () => BODY_FIELD; get g() { return BODY_GETTER } }
export function h(a: number /* COMMENT_TS */) { return BODY_FN }
""",
}


@pytest.mark.parametrize("path", sorted(CASES))
def test_signatures_never_contain_bodies_or_comments(path):
    facts = parse_source(plugin_for(path), path, CASES[path].encode())
    assert facts.error is None
    leaks = [s.signature for s in facts.symbols if "BODY_" in s.signature or "COMMENT_" in s.signature]
    assert leaks == []


def test_signatures_keep_the_declaration():
    sigs = {s.qualname: s.signature for path, src in CASES.items()
            for s in parse_source(plugin_for(path), path, src.encode()).symbols}
    assert sigs["S.body"] == "public var body: some View"
    assert sigs["top"] == "val top: String"
    assert sigs["A.f"] == "fun f(a: Int, b: Int)"
    assert sigs["f"] == "def f(a, b)"
    assert sigs["State"].startswith("data class State(val a: Int")
