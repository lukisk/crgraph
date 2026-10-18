"""Optional Claude summary. Sends only the change description (names, statuses, findings) — never code."""
from __future__ import annotations

import json
from typing import Any

import anthropic

from .config import ExplainConfig

SYSTEM = (
    "You review software changes at the architecture level. You receive a JSON description of a change: "
    "which modules, files and symbols were added, removed, modified, moved or impacted, how dependencies "
    "changed, and automated findings. Write at most 120 words of plain prose: first what the change does "
    "architecturally, then the one to three most important risks for a reviewer. Refer to modules by name. "
    "Use only facts present in the JSON. No headings, no lists."
)
FALLBACK_MODELS = frozenset({"claude-fable-5-1", "claude-opus-5-5", "claude-opus-5", "claude-sonnet-5-5"})
MAX_PAYLOAD_CHARS = 400_000


def _without_signatures(detail: str) -> str:
    """Finding details quote signatures on 'before:'/'after:' lines; drop those lines."""
    return "\n".join(line for line in detail.splitlines() if not line.startswith(("before:", "after:")))


def build_payload(doc: dict[str, Any], include_signatures: bool) -> str:
    keys = ["id", "kind", "name", "module", "status", "path", "from_path"]
    if include_signatures:
        keys += ["signature_before", "signature_after"]
    nodes = [{k: n.get(k) for k in keys} for n in doc["nodes"] if n["status"] != "unchanged"]
    payload = {
        "range": doc["range"]["label"],
        "stats": doc["stats"],
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


def explain(doc: dict[str, Any], cfg: ExplainConfig, client: Any = None) -> tuple[str | None, str | None]:
    """(summary, warning). Never raises: the report is still produced when the summary fails."""
    try:
        client = client or anthropic.Anthropic()
        extra: dict[str, Any] = {}
        if cfg.model in FALLBACK_MODELS:
            extra = {"betas": ["server-side-fallback-2026-07-01"], "fallbacks": "default"}
        response = client.beta.messages.create(
            model=cfg.model,
            max_tokens=16000,
            output_config={"effort": "low"},
            system=SYSTEM,
            messages=[{"role": "user", "content": build_payload(doc, cfg.include_signatures)}],
            **extra,
        )
    except anthropic.AuthenticationError:
        return None, "explain: no valid Anthropic credentials (set ANTHROPIC_API_KEY or run `ant auth login`)"
    except anthropic.RateLimitError:
        return None, "explain: rate limited by the Anthropic API, try again later"
    except anthropic.APIStatusError as exc:
        return None, f"explain: Anthropic API error {exc.status_code}: {exc.message}"
    except anthropic.APIConnectionError:
        return None, "explain: could not reach the Anthropic API"
    except anthropic.AnthropicError as exc:  # e.g. no credentials configured at all
        return None, f"explain: {exc}"
    if response.stop_reason == "refusal":
        return None, "explain: the model declined to summarise this change"
    text = "".join(block.text for block in response.content if block.type == "text").strip()
    return (text or None), (None if text else "explain: the model returned no text")
