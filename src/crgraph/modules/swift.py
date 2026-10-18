"""Swift module detection: Package.swift targets, plus the `<Module>/Sources/` directory convention."""
from __future__ import annotations

import re
from pathlib import PurePosixPath

from .model import ModuleInfo, ModuleMap, Reader, decode, join, parent_dir

TARGET_START = re.compile(r"\.(target|testTarget|executableTarget)\s*\(")
NAME = re.compile(r"\s*name:\s*\"([^\"]+)\"")
PATH = re.compile(r"\bpath:\s*\"([^\"]+)\"")
DEPENDENCIES = re.compile(r"\bdependencies:\s*\[")
PRODUCT = re.compile(r"\.product\(\s*name:\s*\"([^\"]+)\"\s*,\s*package:\s*\"([^\"]+)\"")
TARGET_REF = re.compile(r"\.(?:target|byName)\(\s*name:\s*\"([^\"]+)\"")
PLAIN = re.compile(r"(?<![:\w])\"([^\"]+)\"")


def _balanced(src: str, start: int, open_ch: str, close_ch: str) -> str:
    """Text from `start` (just after an opening bracket) to its matching closing bracket."""
    depth, i, in_str = 1, start, False
    while i < len(src) and depth:
        ch = src[i]
        if ch == '"' and src[i - 1] != "\\":
            in_str = not in_str
        elif not in_str and ch == open_ch:
            depth += 1
        elif not in_str and ch == close_ch:
            depth -= 1
        i += 1
    return src[start:i - 1]


def _targets(src: str) -> list[tuple[str, str, str | None, list[str], list[str]]]:
    """(kind, name, path, local deps, external deps) for each target in a Package.swift."""
    out = []
    for m in TARGET_START.finditer(src):
        body = _balanced(src, m.end(), "(", ")")
        name = NAME.match(body)
        if not name:
            continue
        path = PATH.search(body)
        local: list[str] = []
        external: list[str] = []
        dep = DEPENDENCIES.search(body)
        if dep:
            deps = _balanced(body, dep.end(), "[", "]")
            external = [f"swiftpm:{pkg}/{prod}" for prod, pkg in PRODUCT.findall(deps)]
            cleaned = PRODUCT.sub("", deps)
            local = TARGET_REF.findall(cleaned) + PLAIN.findall(TARGET_REF.sub("", cleaned))
        out.append((m.group(1), name.group(1), path.group(1) if path else None, local, external))
    return out


def detect(files: list[str], read: Reader, mm: ModuleMap) -> None:
    swift_files = [f for f in files if f.endswith(".swift")]
    if not swift_files:
        return
    packages = [f for f in files if PurePosixPath(f).name == "Package.swift"]
    pending: list[tuple[str, list[str], list[str]]] = []
    for pkg, raw in read(packages).items():
        base = parent_dir(pkg)
        for kind, name, path, local, external in _targets(decode(raw)):
            default = f"Tests/{name}" if kind == "testTarget" else f"Sources/{name}"
            mm.add(ModuleInfo(name, join(base, path or default), "swiftpm"))
            pending.append((name, local, external))
    for name, local, external in pending:
        for dep in local:
            if dep in mm.modules and dep != name:
                mm.deps.add((name, dep))
            elif dep not in mm.modules:
                external.append(f"swiftpm:{dep}")
        if external:
            mm.external.setdefault(name, set()).update(external)
    for f in swift_files:
        parts = f.split("/")
        if "Sources" in parts[1:-1] and mm.find(f) is None:
            i = parts.index("Sources", 1)
            mm.add(ModuleInfo(parts[i - 1], "/".join(parts[:i]), "swift"))
