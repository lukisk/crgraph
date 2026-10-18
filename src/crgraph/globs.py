"""Glob matching with `**`, `*`, `?` and `{a,b}` (used for paths and module ids)."""
from __future__ import annotations

import re
from collections.abc import Iterable

_cache: dict[str, re.Pattern[str]] = {}


def _translate(pattern: str) -> str:
    out: list[str] = []
    i = 0
    while i < len(pattern):
        if pattern.startswith("**/", i):
            out.append("(?:.*/)?")
            i += 3
            continue
        if pattern.startswith("**", i):
            out.append(".*")
            i += 2
            continue
        ch = pattern[i]
        if ch == "*":
            out.append("[^/]*")
        elif ch == "?":
            out.append("[^/]")
        elif ch == "{" and "}" in pattern[i:]:
            end = pattern.index("}", i)
            out.append("(?:" + "|".join(_translate(p) for p in pattern[i + 1:end].split(",")) + ")")
            i = end + 1
            continue
        else:
            out.append(re.escape(ch))
        i += 1
    return "".join(out)


def glob_match(pattern: str, text: str) -> bool:
    rx = _cache.get(pattern)
    if rx is None:
        rx = _cache[pattern] = re.compile(_translate(pattern) + r"\Z")
    return rx.match(text) is not None


def match_any(patterns: Iterable[str], text: str) -> bool:
    return any(glob_match(p, text) for p in patterns)


def literal_prefix(pattern: str) -> str:
    """Directory part of a glob before its first wildcard: 'app/**/x' -> 'app'."""
    cut = min((i for i, ch in enumerate(pattern) if ch in "*?{"), default=len(pattern))
    head = pattern[:cut]
    return head.rstrip("/") if cut == len(pattern) else head.rpartition("/")[0]
