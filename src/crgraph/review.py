"""The `--explain` architecture review: its line format, and parsing a model's answer into labelled items."""
from __future__ import annotations

import re
from typing import Any

VERDICTS = ("Good", "Acceptable", "Needs work")
LABELS = ("Verdict", "Architecture", "Coupling & cohesion", "SOLID", "DRY", "Testability", "API & compatibility",
          "Top risks", "Suggestions")
# models may decorate a label line with markdown: "- **Architecture:** ...", "### DRY: ..."
_LINE = re.compile(r"^[\s#>*_-]*(" + "|".join(re.escape(label) for label in LABELS) + r")[\s*_]*:[\s*_]*(.*)$", re.I)
_VERDICT = re.compile(r"^(" + "|".join(VERDICTS) + r")\b[\s.:—–-]*(.*)$", re.I)


def parse_review(text: str | None) -> dict[str, Any] | None:
    """{"verdict", "reason", "items": [[label, text], ...]}, or None when the answer does not use the format
    (the renderers then show it as plain text)."""
    if not text:
        return None
    canonical = {label.lower(): label for label in LABELS}
    items: list[list[str]] = []
    for line in text.splitlines():
        m = _LINE.match(line)
        if m:
            items.append([canonical[m.group(1).lower()], m.group(2).strip()])
        elif items and line.strip():
            items[-1][1] = f"{items[-1][1]} {line.strip()}".strip()  # a wrapped continuation line
    verdict = reason = None
    for label, value in items:
        if label == "Verdict":
            m = _VERDICT.match(value)
            if m:
                verdict = next(v for v in VERDICTS if v.lower() == m.group(1).lower())
                reason = m.group(2).strip() or None
            else:
                reason = value or None
    items = [item for item in items if item[0] != "Verdict"]
    if not items and verdict is None:
        return None
    return {"verdict": verdict, "reason": reason, "items": items}
