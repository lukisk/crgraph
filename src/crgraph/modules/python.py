"""Python module detection: pyproject.toml projects (+ requirements.txt for external deps)."""
from __future__ import annotations

import re
import tomllib
from pathlib import PurePosixPath

from .model import ModuleInfo, ModuleMap, Reader, decode, parent_dir

REQUIREMENT = re.compile(r"\s*([A-Za-z0-9][A-Za-z0-9._\-]*)")


def _name(spec: str) -> str | None:
    m = REQUIREMENT.match(spec)
    return m.group(1).lower() if m else None


def detect(files: list[str], read: Reader, mm: ModuleMap) -> None:
    tomls = [f for f in files if PurePosixPath(f).name == "pyproject.toml"]
    reqs = [f for f in files if PurePosixPath(f).name == "requirements.txt"]
    contents = read(tomls + reqs)
    declared: dict[str, list[str]] = {}
    for path in tomls:
        try:
            doc = tomllib.loads(decode(contents.get(path)))
        except tomllib.TOMLDecodeError:
            continue
        project = doc.get("project", {}) if isinstance(doc.get("project"), dict) else {}
        poetry = doc.get("tool", {}).get("poetry", {}) if isinstance(doc.get("tool"), dict) else {}
        root = parent_dir(path)
        mid = project.get("name") or poetry.get("name") or root or "(root)"
        mm.add(ModuleInfo(mid, root, "python"))
        specs = [d for d in project.get("dependencies", []) if isinstance(d, str)]
        for group in project.get("optional-dependencies", {}).values():
            specs += [d for d in group if isinstance(d, str)]
        names = [n for n in map(_name, specs) if n]
        names += [k.lower() for k in poetry.get("dependencies", {}) if k != "python"]
        declared[mid] = names
    for path in reqs:
        mid = mm.find(path)
        if mid is None or mm.modules[mid].system != "python":
            continue
        lines = [ln for ln in decode(contents.get(path)).splitlines() if ln.strip() and not ln.lstrip().startswith(("#", "-"))]
        declared.setdefault(mid, []).extend(n for n in map(_name, lines) if n)
    for mid, names in declared.items():
        for name in names:
            if name in mm.modules and mm.modules[name].system == "python" and name != mid:
                mm.deps.add((mid, name))
            else:
                mm.external.setdefault(mid, set()).add(f"pip:{name}")
