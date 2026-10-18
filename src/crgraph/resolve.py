"""Resolving import statements to files and modules at one ref."""
from __future__ import annotations

import posixpath
import re
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import PurePosixPath

from .globs import match_any
from .model import FileFacts
from .modules import ModuleMap

KOTLIN_PACKAGE_GREP = "^[[:space:]]*package[[:space:]]"
KOTLIN_PACKAGE_LINE = re.compile(r"\s*package\s+([\w.`]+)")
TS_SUFFIXES = (".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs", ".mts", ".cts")


@dataclass(frozen=True)
class ImportTarget:
    raw: str
    files: frozenset[str]
    modules: frozenset[str]


class RepoIndex:
    """Lookup tables for one ref. `package_lines` is only called when a Kotlin import needs it."""

    def __init__(self, files: list[str], modules: ModuleMap, package_lines: Callable[[], list[tuple[str, str]]],
                 test_patterns: list[str] | tuple[str, ...] = ()):
        self.test_patterns = list(test_patterns)
        self.files = set(files)
        self.modules = modules
        self._package_lines = package_lines
        self._kotlin: dict[str, set[str]] | None = None
        self._py_roots = sorted({""} | {r for m in modules.modules.values() if m.system == "python"
                                        for r in (m.root, f"{m.root}/src".lstrip("/"))})

    def kotlin_package(self, package: str) -> set[str]:
        if self._kotlin is None:
            self._kotlin = {}
            for path, line in self._package_lines():
                m = KOTLIN_PACKAGE_LINE.match(line)
                if m:
                    self._kotlin.setdefault(m.group(1).replace("`", ""), set()).add(path)
        return self._kotlin.get(package, set())

    def resolve(self, facts: FileFacts) -> list[ImportTarget]:
        resolver = {"kotlin": self._kotlin_import, "swift": self._swift_import,
                    "typescript": self._ts_import, "python": self._py_import}.get(facts.lang)
        if resolver is None:
            return []
        targets = [t for t in (resolver(raw, facts) for raw in facts.imports) if t is not None]
        if self.test_patterns and not match_any(self.test_patterns, facts.path):
            targets = [ImportTarget(t.raw, frozenset(f for f in t.files if not match_any(self.test_patterns, f)), t.modules)
                       for t in targets]  # main code never depends on test sources
        return targets

    def _target(self, raw: str, files: set[str], *, module_only: bool = False) -> ImportTarget | None:
        if not files:
            return None
        modules = frozenset(self.modules.module_of(f) for f in files)
        return ImportTarget(raw, frozenset() if module_only else frozenset(files), modules)

    def _kotlin_import(self, raw: str, facts: FileFacts) -> ImportTarget | None:
        if raw.endswith(".*"):
            return self._target(raw, self.kotlin_package(raw[:-2]), module_only=True)
        package, _, last = raw.rpartition(".")
        files = self.kotlin_package(package)
        if files:
            named = {f for f in files if PurePosixPath(f).stem == last}
            # a top-level function or constant: we know the package, not the file
            return self._target(raw, named) if named else self._target(raw, files, module_only=True)
        outer_package, _, outer = package.rpartition(".")  # nested type: a.b.Outer.Inner
        return self._target(raw, {f for f in self.kotlin_package(outer_package) if PurePosixPath(f).stem == outer})

    def _swift_import(self, raw: str, facts: FileFacts) -> ImportTarget | None:
        info = self.modules.modules.get(raw)
        if info is None or info.system not in ("swift", "swiftpm"):
            return None
        return ImportTarget(raw, frozenset(), frozenset({raw}))

    def _ts_import(self, raw: str, facts: FileFacts) -> ImportTarget | None:
        if raw.startswith("."):
            base = posixpath.normpath(posixpath.join(posixpath.dirname(facts.path), raw))
            candidates = [base, *(base + s for s in TS_SUFFIXES), *(f"{base}/index{s}" for s in TS_SUFFIXES)]
            return self._target(raw, next(({c} for c in candidates if c in self.files), set()))
        name = "/".join(raw.split("/")[:2]) if raw.startswith("@") else raw.split("/")[0]
        info = self.modules.modules.get(name)
        if info is None or info.system != "npm":
            return None
        return ImportTarget(raw, frozenset(), frozenset({name}))

    def _py_import(self, raw: str, facts: FileFacts) -> ImportTarget | None:
        if raw.startswith("."):
            dots = len(raw) - len(raw.lstrip("."))
            pkg_dir = posixpath.dirname(facts.path)
            for _ in range(dots - 1):
                pkg_dir = posixpath.dirname(pkg_dir)
            rest = raw[dots:]
            bases = [posixpath.join(pkg_dir, *rest.split(".")) if rest else pkg_dir]
        else:
            bases = [posixpath.join(root, *raw.split(".")) for root in self._py_roots]
        for base in bases:
            for candidate in (f"{base}.py", f"{base}/__init__.py"):
                if candidate.lstrip("/") in self.files:
                    return self._target(raw, {candidate.lstrip("/")})
        return None
