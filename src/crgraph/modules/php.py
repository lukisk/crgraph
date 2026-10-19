"""PHP module detection: composer.json packages, and every directory under a PSR-4 autoload root as a namespace
module (`App\\Service` for src/Service), so imports resolve to files and the module graph shows namespaces."""
from __future__ import annotations

import json
from pathlib import PurePosixPath

from .model import ModuleInfo, ModuleMap, Reader, decode, join, parent_dir

PLATFORM = ("php", "ext-", "lib-", "composer-")  # requirements on the runtime, not on packages


def _psr4(data: dict) -> list[tuple[str, str]]:
    out = []
    for section in ("autoload", "autoload-dev"):
        mapping = data.get(section, {}).get("psr-4", {}) if isinstance(data.get(section), dict) else {}
        for prefix, dirs in mapping.items() if isinstance(mapping, dict) else []:
            for d in [dirs] if isinstance(dirs, str) else dirs if isinstance(dirs, list) else []:
                if isinstance(d, str):
                    out.append((prefix.strip("\\"), d.strip("/")))
    return out


def detect(files: list[str], read: Reader, mm: ModuleMap) -> None:
    manifests = [f for f in files if PurePosixPath(f).name == "composer.json"]
    packages: dict[str, dict] = {}
    bases: list[tuple[str, str]] = []  # (namespace prefix, directory)
    for path, raw in read(manifests).items():
        try:
            data = json.loads(decode(raw))
        except ValueError:
            continue
        if not isinstance(data, dict):
            continue
        root = parent_dir(path)
        name = data.get("name") if isinstance(data.get("name"), str) else (root or "(root)")
        mm.add(ModuleInfo(name, root, "composer"))
        packages[name] = data
        bases += [(prefix, join(root, d)) for prefix, d in _psr4(data)]
    bases.sort(key=lambda b: -len(b[1]))  # the deepest autoload root wins
    for f in files:
        if not f.endswith(".php"):
            continue
        directory = parent_dir(f)
        for prefix, base in bases:
            if base == "" or directory == base or directory.startswith(base + "/"):
                rel = directory[len(base):].strip("/").replace("/", "\\")
                namespace = "\\".join(p for p in (prefix, rel) if p) or "(root)"
                mm.add(ModuleInfo(namespace, directory, "php"))
                break
    for name, data in packages.items():
        for section in ("require", "require-dev"):
            deps = data.get(section)
            for dep in deps if isinstance(deps, dict) else {}:
                if dep.startswith(PLATFORM):
                    continue
                info = mm.modules.get(dep)
                if info is not None and info.system == "composer":
                    if dep != name:
                        mm.deps.add((name, dep))
                else:
                    mm.external.setdefault(name, set()).add(f"composer:{dep}")
