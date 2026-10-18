"""npm/yarn/pnpm module detection: every package.json (except workspace aggregators at the repo root)."""
from __future__ import annotations

import json
from pathlib import PurePosixPath

from .model import ModuleInfo, ModuleMap, Reader, decode, parent_dir

SECTIONS = ("dependencies", "devDependencies", "peerDependencies", "optionalDependencies")


def detect(files: list[str], read: Reader, mm: ModuleMap) -> None:
    manifests = [f for f in files if PurePosixPath(f).name == "package.json"]
    packages: dict[str, dict] = {}
    for path, raw in read(manifests).items():
        try:
            data = json.loads(decode(raw))
        except ValueError:
            continue
        if not isinstance(data, dict):
            continue
        root = parent_dir(path)
        if not root and "workspaces" in data:
            continue  # the root of a workspace only aggregates its packages
        name = data.get("name") if isinstance(data.get("name"), str) else (root or "(root)")
        mm.add(ModuleInfo(name, root, "npm"))
        packages[name] = data
    for name, data in packages.items():
        for section in SECTIONS:
            deps = data.get(section)
            for dep in deps if isinstance(deps, dict) else {}:
                info = mm.modules.get(dep)
                if info is not None and info.system == "npm":
                    if dep != name:
                        mm.deps.add((name, dep))
                else:
                    mm.external.setdefault(name, set()).add(f"npm:{dep}")
