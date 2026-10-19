"""Optional Claude summary. Sends only the change description (names, statuses, findings) — never code."""
from __future__ import annotations

import json
import os
import subprocess
from typing import Any

import anthropic

from .config import ExplainConfig
from .review import LABELS, VERDICTS

SYSTEM = f"""You are a principal software architect reviewing a code change for a colleague who must decide \
whether it is in good shape. You never see code. You get a JSON description of the change:
- nodes: modules, files and symbols that were added, removed, modified, moved or impacted (unchanged code that uses \
changed code), with visibility, signatures, lines of code and cyclomatic complexity before and after, and whether \
they are test code
- edges: dependencies that appeared or disappeared (depends_on between modules; imports, calls, references and \
inherits between files and symbols)
- overview: review size, production vs test lines, a per-module table and the files to review first
- findings: automated checks (new cycles, rule violations, API breaks, new dependencies, coupling, complexity, test \
gaps, blast radius, dead code, hotspots)
- hints.same_name_elsewhere: new or changed types and top-level functions whose name also exists in another file

Judge the change against modern engineering principles: clear module boundaries, layering and dependency \
direction; low coupling and high cohesion; SOLID as far as structure shows it (oversized types or functions, many \
responsibilities, depending on implementations instead of abstractions); DRY (duplicated names, signatures or \
parallel implementations, but say when a split is a legitimate platform or layer boundary); testability (tests \
changed with the code, test share of new lines, complexity, injectable dependencies); API stability for callers.

Base every statement on facts in the JSON and name the modules, types or numbers you rely on. Never guess about \
code you cannot see: when the structure does not show something, say "not visible from structure" and move on. Be \
direct and specific; no generic advice.

Answer with exactly these lines, in this order, each at most two sentences, plain text without markdown:
{chr(10).join(f"{label}: ..." for label in LABELS)}
The Verdict line starts with one of {", ".join(VERDICTS)}, then a dash and the main reason."""
FALLBACK_MODELS = frozenset({"claude-fable-5-1", "claude-opus-5-5", "claude-opus-5", "claude-sonnet-5-5"})
MAX_PAYLOAD_CHARS = 400_000
PLAIN_MAX_TOKENS = 4096  # room for a reasoning model's thinking before the ~120 words
TOKEN_COMMAND_TIMEOUT = 30


def _without_signatures(detail: str) -> str:
    """Finding details quote signatures on 'before:'/'after:' lines; drop those lines."""
    return "\n".join(line for line in detail.splitlines() if not line.startswith(("before:", "after:")))


TYPE_KINDS = ("class", "interface", "object", "enum")


def same_name_elsewhere(doc: dict[str, Any]) -> dict[str, list[str]]:
    """DRY hint: new or changed non-test types and top-level functions whose name also exists in another file."""
    nodes = {n["id"]: n for n in doc["nodes"]}

    def candidate(n: dict[str, Any]) -> bool:
        parent = nodes.get(n.get("parent") or "")
        return n["kind"] in TYPE_KINDS or (n["kind"] == "function" and parent is not None and parent["kind"] == "file")

    paths: dict[tuple[str, str], set[str]] = {}
    for n in doc["nodes"]:
        if n.get("path") and candidate(n):
            paths.setdefault((n["kind"], n["name"]), set()).add(n["path"])
    out = {}
    for n in doc["nodes"]:
        if n["status"] in ("added", "modified") and not n.get("test") and candidate(n):
            others = sorted(paths[(n["kind"], n["name"])] - {n["path"]})
            if others:
                out[f"{n['name']} ({n['path']})"] = others[:5]
    return out


def build_payload(doc: dict[str, Any], include_signatures: bool) -> str:
    keys = ["id", "kind", "name", "module", "status", "path", "from_path", "visibility", "test",
            "loc_before", "loc_after", "complexity_before", "complexity_after"]
    if include_signatures:
        keys += ["signature_before", "signature_after"]
    nodes = [{k: n.get(k) for k in keys if n.get(k) is not None} for n in doc["nodes"] if n["status"] != "unchanged"]
    payload = {
        "range": doc["range"]["label"],
        "stats": doc["stats"],
        "overview": doc.get("overview") or {},
        "hints": {"same_name_elsewhere": same_name_elsewhere(doc)},
        "findings": [{"severity": f["severity"], "rule": f["rule"], "title": f["title"],
                      "detail": f["detail"] if include_signatures else _without_signatures(f["detail"])}
                     for f in doc["findings"]],
        "nodes": nodes,
        "edges": [e for e in doc["edges"] if e["status"] != "unchanged"],
    }
    text = json.dumps(payload, ensure_ascii=False)
    if len(text) > MAX_PAYLOAD_CHARS:  # very large changes: keep the module and file level only
        payload["nodes"] = [n for n in nodes if n["kind"] in ("module", "file")]
        payload["edges"] = [e for e in payload["edges"] if e["kind"] in ("depends_on", "imports")]
        text = json.dumps(payload, ensure_ascii=False)[:MAX_PAYLOAD_CHARS]
    return text


def client_args(cfg: ExplainConfig) -> tuple[dict[str, Any], str | None]:
    """(Anthropic client arguments, warning) from the personal settings. Without them the SDK's own lookup applies:
    ANTHROPIC_BASE_URL, ANTHROPIC_API_KEY / ANTHROPIC_AUTH_TOKEN, then the `ant auth login` profile."""
    args: dict[str, Any] = {"base_url": cfg.base_url} if cfg.base_url else {}
    token = cfg.auth_token
    if cfg.auth_token_env:
        token = os.environ.get(cfg.auth_token_env)
        if not token:
            return {}, f"explain: {cfg.auth_token_env} is not set"
    elif cfg.auth_token_command:
        try:  # the user's own config: a shell command, like a git credential helper
            proc = subprocess.run(cfg.auth_token_command, shell=True, capture_output=True, text=True,
                                  timeout=TOKEN_COMMAND_TIMEOUT)
        except subprocess.TimeoutExpired:
            return {}, "explain: auth_token_command timed out"
        token = proc.stdout.strip()
        if proc.returncode or not token:  # never echo its output: it may hold part of the token
            return {}, f"explain: auth_token_command failed (exit {proc.returncode})"
    if token:
        args["api_key" if cfg.token_header == "x-api-key" else "auth_token"] = token
    return args, None


def explain(doc: dict[str, Any], cfg: ExplainConfig, client: Any = None) -> tuple[str | None, str | None]:
    """(summary, warning). Never raises: the report is still produced when the summary fails."""
    where = cfg.base_url or "the Anthropic API"
    messages = [{"role": "user", "content": build_payload(doc, cfg.include_signatures)}]
    try:
        if client is None:
            args, warning = client_args(cfg)
            if warning:
                return None, warning
            client = anthropic.Anthropic(**args)
        if cfg.plain:
            response = client.messages.create(model=cfg.model, max_tokens=PLAIN_MAX_TOKENS, system=SYSTEM,
                                              messages=messages)
        else:
            extra: dict[str, Any] = {}
            if cfg.model in FALLBACK_MODELS:
                extra = {"betas": ["server-side-fallback-2026-07-01"], "fallbacks": "default"}
            response = client.beta.messages.create(
                model=cfg.model,
                max_tokens=16000,
                output_config={"effort": "medium"},
                system=SYSTEM,
                messages=messages,
                **extra,
            )
    except anthropic.AuthenticationError:
        if cfg.base_url:
            return None, f"explain: {where} rejected the credentials"
        return None, "explain: no valid Anthropic credentials (set ANTHROPIC_API_KEY or run `ant auth login`)"
    except anthropic.RateLimitError:
        return None, f"explain: rate limited by {where}, try again later"
    except anthropic.APIStatusError as exc:
        return None, f"explain: error {exc.status_code} from {where}: {exc.message}"
    except anthropic.APIConnectionError:
        return None, f"explain: could not reach {where}"
    except anthropic.AnthropicError as exc:  # e.g. no credentials configured at all
        return None, f"explain: {exc}"
    if response.stop_reason == "refusal":
        return None, "explain: the model declined to summarise this change"
    text = "".join(block.text for block in response.content if block.type == "text").strip()
    return (text or None), (None if text else "explain: the model returned no text")
