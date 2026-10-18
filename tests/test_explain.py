from types import SimpleNamespace

import anthropic
import httpx2

from crgraph.config import ExplainConfig
from crgraph.explain import build_payload, explain

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
    return SimpleNamespace(beta=SimpleNamespace(messages=messages))


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
    assert messages.kwargs["output_config"] == {"effort": "low"}


def test_explain_without_fallback_support_omits_it():
    messages = FakeMessages(text_response("ok"))
    explain(DOC, ExplainConfig(model="claude-haiku-5-5"), client(messages))
    assert "fallbacks" not in messages.kwargs and "betas" not in messages.kwargs


def test_refusal_and_errors_become_warnings():
    assert explain(DOC, ExplainConfig(), client(FakeMessages(text_response("", stop="refusal"))))[1].startswith("explain: the model declined")
    request = httpx2.Request("POST", "https://api.anthropic.com/v1/messages")
    err = anthropic.APIConnectionError(request=request)
    assert explain(DOC, ExplainConfig(), client(FakeMessages(error=err))) == (None, "explain: could not reach the Anthropic API")
