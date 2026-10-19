import json
from types import SimpleNamespace

import anthropic
import httpx2

from crgraph.config import ExplainConfig
from crgraph.explain import SYSTEM, build_payload, client_args, explain, same_name_elsewhere
from crgraph.review import parse_review

DOC = {
    "range": {"label": "main...feat"},
    "stats": {"modules": {"modified": 1}},
    "nodes": [
        {"id": "module::core", "kind": "module", "name": ":core", "module": ":core", "status": "modified", "path": "core",
         "from_path": None, "signature_before": None, "signature_after": None},
        {"id": "sym:a#f(0)", "kind": "function", "name": "f", "module": ":core", "status": "modified", "path": "a",
         "from_path": None, "signature_before": "fun f(): Int", "signature_after": "fun f(): Long"},
        {"id": "sym:a#g(0)", "kind": "function", "name": "g", "module": ":core", "status": "unchanged", "path": "a",
         "from_path": None, "signature_before": "fun g()", "signature_after": "fun g()"},
    ],
    "edges": [{"src": "a", "dst": "b", "kind": "calls", "status": "unchanged"}],
    "findings": [{"severity": "red", "rule": "api-change", "title": "t", "targets": [],
                  "detail": "before: fun f(): Int\nafter:  fun f(): Long\nused from 2 place(s) in other modules"}],
}


class FakeMessages:
    def __init__(self, response=None, error=None):
        self.response, self.error, self.kwargs = response, error, None

    def create(self, **kwargs):
        self.kwargs = kwargs
        if self.error:
            raise self.error
        return self.response


def client(messages):
    return SimpleNamespace(beta=SimpleNamespace(messages=messages), messages=messages)


def text_response(text, stop="end_turn"):
    return SimpleNamespace(stop_reason=stop, content=[SimpleNamespace(type="thinking"), SimpleNamespace(type="text", text=text)])


def test_payload_has_only_changes_and_optional_signatures():
    with_sigs = build_payload(DOC, include_signatures=True)
    assert "fun f(): Long" in with_sigs and '"g"' not in with_sigs and '"calls"' not in with_sigs
    without = build_payload(DOC, include_signatures=False)
    assert "fun f()" not in without and "used from 2 place(s)" in without


def test_explain_returns_text_and_uses_fallbacks_for_opus():
    messages = FakeMessages(text_response("Moves X into Y."))
    text, warning = explain(DOC, ExplainConfig(), client(messages))
    assert (text, warning) == ("Moves X into Y.", None)
    assert messages.kwargs["model"] == "claude-opus-5-5"
    assert messages.kwargs["fallbacks"] == "default"
    assert messages.kwargs["output_config"] == {"effort": "medium"}


def test_explain_without_fallback_support_omits_it():
    messages = FakeMessages(text_response("ok"))
    explain(DOC, ExplainConfig(model="claude-haiku-5-5"), client(messages))
    assert "fallbacks" not in messages.kwargs and "betas" not in messages.kwargs


def test_refusal_and_errors_become_warnings():
    assert explain(DOC, ExplainConfig(), client(FakeMessages(text_response("", stop="refusal"))))[1].startswith("explain: the model declined")
    request = httpx2.Request("POST", "https://api.anthropic.com/v1/messages")
    err = anthropic.APIConnectionError(request=request)
    assert explain(DOC, ExplainConfig(), client(FakeMessages(error=err))) == (None, "explain: could not reach the Anthropic API")


def test_plain_mode_sends_only_standard_fields():
    messages = FakeMessages(text_response("ok"))
    explain(DOC, ExplainConfig(model="gpt-proxy", plain=True), client(messages))
    assert set(messages.kwargs) == {"model", "max_tokens", "system", "messages"}


def test_client_args_read_the_token_from_a_variable_or_a_command(monkeypatch):
    monkeypatch.setenv("LLM_PROXY_TOKEN", "tok-env")
    cfg = ExplainConfig(base_url="https://proxy.example", auth_token_env="LLM_PROXY_TOKEN")
    assert client_args(cfg) == ({"base_url": "https://proxy.example", "auth_token": "tok-env"}, None)
    cfg = ExplainConfig(auth_token_command="printf 'tok-cmd\\n'", token_header="x-api-key")
    assert client_args(cfg) == ({"api_key": "tok-cmd"}, None)
    assert client_args(ExplainConfig(auth_token="tok-file")) == ({"auth_token": "tok-file"}, None)
    assert client_args(ExplainConfig()) == ({}, None)  # the SDK's own lookup


def test_missing_token_or_failing_command_is_a_warning_without_the_output(monkeypatch):
    monkeypatch.delenv("NOPE_TOKEN", raising=False)
    assert client_args(ExplainConfig(auth_token_env="NOPE_TOKEN")) == ({}, "explain: NOPE_TOKEN is not set")
    args, warning = client_args(ExplainConfig(auth_token_command="echo secret-part; exit 3"))
    assert args == {} and warning == "explain: auth_token_command failed (exit 3)"
    assert explain(DOC, ExplainConfig(auth_token_env="NOPE_TOKEN")) == (None, "explain: NOPE_TOKEN is not set")


def test_errors_name_the_proxy():
    request = httpx2.Request("POST", "https://proxy.example/v1/messages")
    err = anthropic.APIConnectionError(request=request)
    cfg = ExplainConfig(base_url="https://proxy.example")
    assert explain(DOC, cfg, client(FakeMessages(error=err))) == (None, "explain: could not reach https://proxy.example")


def test_prompt_asks_for_a_verdict_on_principles():
    for words in ("Verdict: ...", "SOLID: ...", "DRY: ...", "Testability: ...", "Good, Acceptable, Needs work",
                  "not visible from structure"):
        assert words in SYSTEM


def test_payload_carries_size_complexity_tests_and_the_overview():
    doc = {**DOC, "overview": {"verdict": "Size S"},
           "nodes": [{**DOC["nodes"][1], "loc_before": 3, "loc_after": 9, "complexity_before": 1, "complexity_after": 4,
                      "visibility": "public", "test": False}]}
    payload = json.loads(build_payload(doc, include_signatures=True))
    node = payload["nodes"][0]
    assert (node["loc_after"], node["complexity_after"], node["test"]) == (9, 4, False)
    assert payload["overview"] == {"verdict": "Size S"} and payload["hints"] == {"same_name_elsewhere": {}}


def test_same_name_elsewhere_flags_possible_duplication():
    def node(nid, kind, name, path, status, parent, test=False):
        return {"id": nid, "kind": kind, "name": name, "path": path, "status": status, "parent": parent, "test": test}
    nodes = [node("file:a.kt", "file", "a.kt", "a.kt", "added", None), node("file:b.kt", "file", "b.kt", "b.kt", "unchanged", None),
             node("sym:a.kt#Price(0)", "class", "Price", "a.kt", "added", "file:a.kt"),
             node("sym:b.kt#Price(0)", "class", "Price", "b.kt", "unchanged", "file:b.kt"),
             node("sym:a.kt#format(1)", "function", "format", "a.kt", "added", "file:a.kt"),
             node("sym:b.kt#format(1)", "function", "format", "b.kt", "unchanged", "file:b.kt"),
             node("sym:a.kt#Price.invoke(0)", "function", "invoke", "a.kt", "added", "sym:a.kt#Price(0)"),
             node("sym:b.kt#Price.invoke(0)", "function", "invoke", "b.kt", "unchanged", "sym:b.kt#Price(0)")]
    assert same_name_elsewhere({"nodes": nodes}) == {"Price (a.kt)": ["b.kt"], "format (a.kt)": ["b.kt"]}  # not members


def test_parse_review_reads_the_format_even_with_markdown_around_it():
    text = ("**Verdict:** needs work — new cycle between :a and :b\n- **Architecture:** :a now depends on :b\n"
            "  and back.\n### DRY: no duplication visible\nTestability: tests changed in :a only")
    assert parse_review(text) == {"verdict": "Needs work", "reason": "new cycle between :a and :b", "items": [
        ["Architecture", ":a now depends on :b and back."], ["DRY", "no duplication visible"],
        ["Testability", "tests changed in :a only"]]}
    assert parse_review("Just a paragraph of prose.") is None
    assert parse_review(None) is None
