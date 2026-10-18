"""Blast radius: unchanged files that mention changed symbols."""
from __future__ import annotations

from .config import Config
from .gitio import Repo
from .globs import match_any
from .lang import plugin_for, source_extensions
from .model import FileFacts, Symbol

MIN_NAME_LENGTH = 3


def _symbols(facts: dict[str, FileFacts], renames: dict[str, str]) -> dict[tuple[str, str], Symbol]:
    out = {}
    for path, f in facts.items():
        new_path = renames.get(path, path)
        for s in f.symbols:
            out[(new_path, s.qualname)] = s
    return out


def changed_symbol_names(before: dict[str, FileFacts], after: dict[str, FileFacts],
                         renames: dict[str, str]) -> set[str]:
    """Outermost declared names of non-private symbols that were added, removed or changed."""
    b, a = _symbols(before, renames), _symbols(after, {})
    names = set()
    for key in b.keys() | a.keys():
        sb, sa = b.get(key), a.get(key)
        sym = sa or sb
        if sym.visibility == "private":
            continue
        if sb and sa and sb.signature == sa.signature and sb.body_hash == sa.body_hash:
            continue
        names.add(sym.qualname.split(".")[0])
    return {n for n in names if len(n) >= MIN_NAME_LENGTH}


def find_impacted_files(repo: Repo, ref: str, names: set[str], exclude: set[str],
                        config: Config) -> tuple[list[str], str | None]:
    """Candidate dependents of the changed names, capped at config.blast_max_files."""
    if not names:
        return [], None
    pathspecs = [f"*{ext}" for ext in source_extensions()]
    hits = sorted(p for p in repo.grep_files(ref, sorted(names), pathspecs)
                  if p not in exclude and plugin_for(p) is not None and not match_any(config.ignore, p))
    if len(hits) > config.blast_max_files:
        warning = (f"blast radius truncated: {len(hits)} candidate files mention changed names, "
                   f"analysed the first {config.blast_max_files}")
        return hits[: config.blast_max_files], warning
    return hits, None
