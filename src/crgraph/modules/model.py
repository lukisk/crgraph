"""Module map: which module a path belongs to, and declared module dependencies."""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field

from ..globs import glob_match, literal_prefix

Reader = Callable[[list[str]], dict[str, bytes | None]]


def decode(data: bytes | None) -> str:
    return data.decode("utf-8", "replace") if data else ""


def parent_dir(path: str) -> str:
    return path.rpartition("/")[0]


def join(base: str, rel: str) -> str:
    rel = rel.strip("/")
    if rel in ("", "."):
        return base
    return f"{base}/{rel}" if base else rel


@dataclass(frozen=True)
class ModuleInfo:
    id: str
    root: str  # directory relative to the repo root; "" is the repo root
    system: str  # gradle|swiftpm|swift|npm|python|config|fallback


@dataclass
class ModuleMap:
    modules: dict[str, ModuleInfo] = field(default_factory=dict)
    deps: set[tuple[str, str]] = field(default_factory=set)
    external: dict[str, set[str]] = field(default_factory=dict)
    overrides: dict[str, str] = field(default_factory=dict)  # glob -> module id
    fallback_depth: int = 2
    _roots: list[tuple[str, str]] = field(default_factory=list)

    def add(self, info: ModuleInfo) -> None:
        if info.id in self.modules:
            return
        self.modules[info.id] = info
        self._roots.append((info.root, info.id))
        self._roots.sort(key=lambda r: -len(r[0]))

    def find(self, path: str) -> str | None:
        """Module whose root contains `path` (deepest wins), ignoring overrides and fallbacks."""
        for root, mid in self._roots:
            if self.modules[mid].system in ("config", "fallback"):
                continue
            if root == "" or path == root or path.startswith(root + "/"):
                return mid
        return None

    def module_of(self, path: str) -> str:
        for pattern, mid in self.overrides.items():
            if glob_match(pattern, path):
                self.add(ModuleInfo(mid, literal_prefix(pattern), "config"))
                return mid
        found = self.find(path)
        if found is not None:
            return found
        root = "/".join(path.split("/")[:-1][: self.fallback_depth])
        mid = root or "(root)"
        self.add(ModuleInfo(mid, root, "fallback"))
        return mid
