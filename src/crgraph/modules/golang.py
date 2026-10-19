"""Go module detection: every package directory under a go.mod is a module named by its import path."""
from __future__ import annotations

import re
from pathlib import PurePosixPath

from .model import ModuleInfo, ModuleMap, Reader, decode, parent_dir

MODULE_LINE = re.compile(r"^\s*module\s+(\S+)", re.M)
REQUIRE_BLOCK = re.compile(r"^\s*require\s*\((.*?)^\s*\)", re.M | re.S)
REQUIRE_LINE = re.compile(r"^\s*require\s+([^\s(]\S*)\s+(.*)$", re.M)


def _requires(text: str) -> list[str]:
    """Direct requirements: `// indirect` ones only follow from other dependencies."""
    lines = [line for block in REQUIRE_BLOCK.findall(text) for line in block.splitlines()]
    lines += [f"{m.group(1)} {m.group(2)}" for m in REQUIRE_LINE.finditer(text)]
    out = []
    for line in lines:
        parts = line.split("//", 1)
        words = parts[0].split()
        if words and not (len(parts) > 1 and parts[1].strip().startswith("indirect")):
            out.append(words[0])
    return out


def _under(path: str, root: str) -> bool:
    return root == "" or path == root or path.startswith(root + "/")


def detect(files: list[str], read: Reader, mm: ModuleMap) -> None:
    manifests = [f for f in files if PurePosixPath(f).name == "go.mod"]
    contents = read(manifests)
    roots: dict[str, str] = {}  # directory -> module path
    requires: dict[str, list[str]] = {}
    for path in manifests:
        text = decode(contents.get(path))
        m = MODULE_LINE.search(text)
        if m:
            roots[parent_dir(path)] = m.group(1)
            requires[m.group(1)] = _requires(text)
    if not roots:
        return
    deepest_first = sorted(roots, key=len, reverse=True)
    for root, module_path in roots.items():
        mm.add(ModuleInfo(module_path, root, "go"))  # the module itself: its go.mod and root package
    for f in files:
        if not f.endswith(".go") or "testdata" in PurePosixPath(f).parts:
            continue
        directory = parent_dir(f)
        root = next((r for r in deepest_first if _under(directory, r)), None)
        if root is None:
            continue
        rel = directory[len(root):].lstrip("/") if root else directory
        mm.add(ModuleInfo(f"{roots[root]}/{rel}" if rel else roots[root], directory, "go"))
    local = set(roots.values())
    for module_path, deps in requires.items():
        for dep in deps:
            if dep in local and dep != module_path:
                mm.deps.add((module_path, dep))
            elif dep not in local:
                mm.external.setdefault(module_path, set()).add(f"go:{dep}")
