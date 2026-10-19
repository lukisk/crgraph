"""summary.md: a Mermaid module diagram + findings table, pasteable into a GitLab MR comment."""
from __future__ import annotations

import re
from typing import Any

# Explicit text colour: the fills are light, so a dark-theme viewer's default light text would vanish on them.
NODE_TEXT = "color:#1f2328,stroke-width:1px"
STATUS_STYLE = {
    "added": f"fill:#d3f5df,stroke:#2e9e5b,{NODE_TEXT}",
    "modified": f"fill:#fbecc8,stroke:#d99a1e,{NODE_TEXT}",
    "removed": f"fill:#f8d4d4,stroke:#d04545,{NODE_TEXT}",
    "moved": f"fill:#e6dcf7,stroke:#8a63d2,{NODE_TEXT}",
    "impacted": f"fill:#ffffff,stroke:#5b7fd6,stroke-dasharray:4 3,{NODE_TEXT}",
    "unchanged": f"fill:#f2f2f2,stroke:#999999,{NODE_TEXT}",
}
# Sibling groups: a light dashed frame in a mid grey that reads on both light and dark backgrounds.
GROUP_STYLE = "fill:transparent,stroke:#8c959f,stroke-width:1px,stroke-dasharray:4 4,color:#8c959f"
SEVERITY_ICON = {"red": "🔴", "yellow": "🟡", "info": "⚪"}
MAX_NODES = 30
MAX_MODULE_ROWS = 15
MIN_GROUP_SIZE = 2
LEGEND = ("_Legend: A → B means A depends on B. Thick arrow: new dependency, dotted: removed, red: part of a "
          "finding. Boxes: green added, yellow modified, red removed, purple moved, dashed blue impacted "
          "(uses changed code), grey unchanged._")
STATUS_ORDER = ["removed", "added", "modified", "moved", "impacted", "unchanged"]


def _label(text: str) -> str:
    """Mermaid node label: one line, no quote or backtick that could end the label or the code fence."""
    return " ".join(text.splitlines()).replace('"', "#quot;").replace("`", "#96;")


MD_SPECIAL = re.compile(r"([\\`*_\[\]!|~])")  # emphasis, code, links/images, tables, strikethrough


def _cell(text: str) -> str:
    """Plain text on one line: no markdown, link, image or HTML can come through from names in the repo."""
    text = " ".join(text.splitlines()).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    return MD_SPECIAL.sub(r"\\\1", text)


def _code(text: str) -> str:
    """A one-line code span that no backtick or newline in `text` can break out of."""
    text = " ".join(text.splitlines())
    fence = "`" * (max((len(r) for r in re.findall(r"`+", text)), default=0) + 1)
    pad = " " if text.startswith("`") or text.endswith("`") else ""
    return f"{fence}{pad}{text}{pad}{fence}"


def _counts(bucket: dict[str, int]) -> str:
    parts = [f"{bucket[s]} {s}" for s in STATUS_ORDER if bucket.get(s) and s != "unchanged"]
    return ", ".join(parts) or "no changes"


def mermaid(doc: dict[str, Any]) -> list[str]:
    modules = {n["id"]: n for n in doc["nodes"] if n["kind"] == "module"}
    keep = {mid for mid, n in modules.items() if n["status"] != "unchanged"}
    if not keep:
        return ["flowchart LR", '  none["no module-level changes"]']
    pairs: dict[tuple[str, str], dict[str, Any]] = {}
    for e in doc["edges"]:
        if e["kind"] != "depends_on" or e["src"] not in modules or e["dst"] not in modules:
            continue
        key = (e["src"], e["dst"])
        if key not in pairs or pairs[key]["status"] == "removed":
            pairs[key] = e
    neighbours = {x for (s, d) in pairs for x in (s, d) if s in keep or d in keep} - keep
    ordered = sorted(keep, key=lambda m: (STATUS_ORDER.index(modules[m]["status"]), m)) + sorted(neighbours)
    shown = ordered[:MAX_NODES]
    alias = {mid: f"m{i}" for i, mid in enumerate(shown)}
    groups = _groups(shown, modules)
    group_of = {mid: gid for gid, (_, members) in groups.items() for mid in members}
    red = {t for f in doc["findings"] if f["severity"] == "red" for t in f["targets"]}
    links = [(src, dst, e["status"], f"{src}->{dst}:depends_on" in red) for (src, dst), e in sorted(pairs.items())
             if src in alias and dst in alias and (src in keep or dst in keep)]
    links = _collapse_group_fan_in(links, groups, group_of, alias)

    lines = ["flowchart LR"]
    for gid, (title, members) in groups.items():
        lines.append(f'  subgraph {gid}["{_label(title)}"]')
        lines += [f"  {_node(alias[mid], _leaf(modules[mid]['name']), modules[mid]['status'])}" for mid in members]
        lines.append("  end")
    lines += [_node(alias[mid], modules[mid]["name"], modules[mid]["status"]) for mid in shown if mid not in group_of]
    for src, dst, status, _ in links:
        arrow = {"removed": "-.->", "added": "==>"}.get(status, "-->")
        lines.append(f"  {src} {arrow} {dst}")
    hidden = len(ordered) - len(shown)
    if hidden:
        lines.append(f'  more["+{hidden} more modules"]')
    lines += [f"  classDef {status} {style}" for status, style in STATUS_STYLE.items()]
    lines += [f"  style {gid} {GROUP_STYLE}" for gid in groups]
    lines += [f"  linkStyle {i} stroke:#d04545,stroke-width:3px" for i, link in enumerate(links) if link[3]]
    return lines


def _node(node_alias: str, name: str, status: str) -> str:
    return f'  {node_alias}("{_label(name)}"):::{status}'


def _split_name(name: str) -> tuple[str, str]:
    """`:a:b:leaf` → (`:a:b`, `leaf`); `ios/build-scripts` → (`ios`, `build-scripts`)."""
    cut = max(name.rfind(":"), name.rfind("/"))
    return (name[:cut], name[cut + 1:]) if cut > 0 else ("", name)


def _leaf(name: str) -> str:
    return _split_name(name)[1]


def _groups(shown: list[str], modules: dict[str, dict[str, Any]]) -> dict[str, tuple[str, list[str]]]:
    """Subgraphs of modules that share a parent path, so each box only needs its short leaf name."""
    by_parent: dict[str, list[str]] = {}
    for mid in shown:
        parent = _split_name(modules[mid]["name"])[0]
        if parent:
            by_parent.setdefault(parent, []).append(mid)
    siblings = [(parent, members) for parent, members in by_parent.items() if len(members) >= MIN_GROUP_SIZE]
    return {f"g{i}": sibling for i, sibling in enumerate(siblings)}


def _collapse_group_fan_in(links: list[tuple[str, str, str, bool]], groups: dict[str, tuple[str, list[str]]],
                           group_of: dict[str, str], alias: dict[str, str]) -> list[tuple[str, str, str, bool]]:
    """One `group → target` edge instead of one per member, when every member has that same plain edge."""
    collapsed: set[tuple[str, str]] = set()
    group_links = []
    for gid, (_, members) in groups.items():
        for dst in sorted({d for s, d, _, _ in links if group_of.get(s) == gid and group_of.get(d) != gid}):
            edges = [link for link in links if link[0] in members and link[1] == dst]
            if len(edges) == len(members) and len({status for _, _, status, _ in edges}) == 1 \
                    and not any(is_red for *_, is_red in edges):
                collapsed |= {(s, d) for s, d, _, _ in edges}
                group_links.append((gid, alias[dst], edges[0][2], False))
    member_links = [(alias[s], alias[d], status, is_red) for s, d, status, is_red in links if (s, d) not in collapsed]
    return group_links + member_links


def _pm(lines: dict[str, int]) -> str:
    return f"+{lines['added']} −{lines['deleted']}" if lines["added"] or lines["deleted"] else "—"


def _symbols(s: dict[str, int]) -> str:
    parts = [f"{sign}{s[k]}" for sign, k in (("+", "added"), ("−", "removed"), ("~", "modified"), ("↷", "moved")) if s[k]]
    return " ".join(parts) or "—"


def overview(ov: dict[str, Any]) -> list[str]:
    if not ov:
        return []
    size = ov["size"]
    out = ["### Overview", "", (f"**Size: {ov['size_label']}** · " if ov["size_label"] else "") + _cell(ov["verdict"]), ""]
    lines = [f"code {_pm(size['code'])}"]
    if size["tests"]["added"] or size["tests"]["deleted"]:
        lines.append(f"tests {_pm(size['tests'])} ({size['test_share']} % of added lines)")
    else:
        lines.append("no test changes")
    if size["other"]["added"] or size["other"]["deleted"]:
        lines.append(f"other {_pm(size['other'])}")
    if size["binary_files"]:
        lines.append(f"{size['binary_files']} binary file(s)")
    out += ["**Lines:** " + " · ".join(lines), ""]
    rows = ov["modules"]
    if rows:
        out += ["| Module | Status | Files | Symbols | Code lines | Test lines | Findings | Used by |",
                "|---|---|---|---|---|---|---|---|"]
        for r in rows[:MAX_MODULE_ROWS]:
            sev = " ".join(f"{SEVERITY_ICON[s]} {r['findings'][s]}" for s in ("red", "yellow") if r["findings"][s]) or "—"
            out.append(f"| {_cell(r['name'])} | {r['status']} | {r['files']} | {_symbols(r['symbols'])} | "
                       f"{_pm(r['lines'])} | {_pm(r['test_lines'])} | {sev} | {r['used_by']} |")
        if len(rows) > MAX_MODULE_ROWS:
            out.append(f"| +{len(rows) - MAX_MODULE_ROWS} more modules | | | | | | | |")
        out.append("")
    quiet = ov.get("other_modules") or []
    if quiet:
        names = ", ".join(_cell(n) for n in quiet[:5]) + (", …" if len(quiet) > 5 else "")
        out += [f"Only docs/config changed in {len(quiet)} more: {names}", ""]
    if ov["start_here"]:
        out += ["**Start here**", ""]
        out += [f"{i}. {_code(f['path'])} — {_cell(', '.join(f['reasons']))}" for i, f in enumerate(ov["start_here"], 1)]
        out.append("")
    return out


VERDICT_ICON = {"Good": "🟢", "Acceptable": "🟡", "Needs work": "🔴"}


def review(doc: dict[str, Any]) -> list[str]:
    r = doc.get("review")
    if not doc.get("explain"):
        return []
    if not r:  # the model did not use the review format: show its text as is
        return [_cell(line) for line in doc["explain"].splitlines()] + [""]
    head = "**AI review**"
    if r["verdict"]:
        head = f"**AI review: {VERDICT_ICON[r['verdict']]} {r['verdict']}**" + (f" — {_cell(r['reason'])}" if r["reason"] else "")
    out = [head, ""]
    if r["items"]:
        out += ["| Aspect | Assessment |", "|---|---|", *(f"| {_cell(label)} | {_cell(text)} |" for label, text in r["items"]), ""]
    return out


def to_markdown(doc: dict[str, Any]) -> str:
    st = doc["stats"]
    sev = st["findings"]
    out = [f"## crgraph · {_code(doc['range']['label'])}", ""]
    out += review(doc)
    out.append(f"**Modules:** {_counts(st['modules'])} · **Files:** {_counts(st['files'])} · "
               f"**Symbols:** {_counts(st['symbols'])} · **Findings:** 🔴 {sev.get('red', 0)} "
               f"🟡 {sev.get('yellow', 0)} ⚪ {sev.get('info', 0)}")
    out += ["", *overview(doc.get("overview") or {}), "```mermaid", *mermaid(doc), "```", LEGEND, ""]
    if doc["findings"]:
        out += ["| | Rule | Finding |", "|---|---|---|"]
        out += [f"| {SEVERITY_ICON[f['severity']]} | {f['rule']} | {_cell(f['title'])} |" for f in doc["findings"]]
    else:
        out.append("No findings.")
    if doc["warnings"]:
        out += ["", "<details><summary>Warnings</summary>", ""]
        out += [f"- {_cell(w)}" for w in doc["warnings"]]
        out += ["", "</details>"]
    return "\n".join(out) + "\n"
