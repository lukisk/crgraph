"""Gradle module detection: settings.gradle(.kts) includes + build.gradle(.kts) dependencies."""
from __future__ import annotations

import re
from pathlib import PurePosixPath

from .model import ModuleInfo, ModuleMap, Reader, decode, join, parent_dir

INCLUDE = re.compile(r"\binclude\s*\(([^)]*)\)|\binclude\s+((?:['\"][^'\"]+['\"]\s*,?\s*)+)")
QUOTED = re.compile(r"['\"]([^'\"]+)['\"]")
PROJECT_DIR = re.compile(
    r"project\(\s*['\"](:[^'\"]+)['\"]\s*\)\.projectDir\s*=\s*(?:File|file)\("
    r"\s*(?:(?:rootProject\.projectDir|rootDir|settingsDir)\s*,\s*)?['\"]([^'\"]+)['\"]\s*\)"
)
PROJECT_DEP = re.compile(r"\bproject\(\s*(?:path\s*=\s*)?['\"](:[^'\"]+)['\"]")
ACCESSOR_DEP = re.compile(r"\bprojects((?:\.[A-Za-z_][A-Za-z0-9_]*)+)")
LIBS_DEP = re.compile(r"\blibs((?:\.[A-Za-z_][A-Za-z0-9_]*)+)")
COORD_DEP = re.compile(
    r"\b\w*(?:[Ii]mplementation|[Aa]pi|[Oo]nly|kapt|ksp)\s*\(\s*['\"]([\w.\-]+:[\w.\-]+)(?::[^'\"]*)?['\"]"
)
SETTINGS = ("settings.gradle.kts", "settings.gradle")
BUILDS = ("build.gradle.kts", "build.gradle")


def strip_comments(src: str) -> str:
    src = re.sub(r"/\*.*?\*/", "", src, flags=re.S)
    return re.sub(r"(?m)(^|\s)//.*$", r"\1", src)


def _camel(segment: str) -> str:
    return re.sub(r"[-_]+([a-zA-Z0-9])", lambda m: m.group(1).upper(), segment)


def accessor_key(gradle_path: str) -> str:
    return ".".join(_camel(s) for s in gradle_path.strip(":").split(":"))


def _includes(src: str) -> list[str]:
    found = []
    for m in INCLUDE.finditer(src):
        found += QUOTED.findall(m.group(1) or m.group(2) or "")
    return [p if p.startswith(":") else f":{p}" for p in found]


def detect(files: list[str], read: Reader, mm: ModuleMap) -> None:
    fileset = set(files)
    settings = [f for f in files if PurePosixPath(f).name in SETTINGS]
    if not settings:
        return
    by_path: dict[tuple[str, str], str] = {}  # (settings dir, gradle path) -> module id
    by_accessor: dict[tuple[str, str], str] = {}
    base_of: dict[str, str] = {}
    build_file: dict[str, str] = {}
    for settings_path, raw in read(settings).items():
        src = strip_comments(decode(raw))
        base = parent_dir(settings_path)
        dirs = {m.group(1): m.group(2) for m in PROJECT_DIR.finditer(src)}
        for gpath in _includes(src):
            root = join(base, dirs.get(gpath, gpath.strip(":").replace(":", "/")))
            mid = f"{base}{gpath}" if base else gpath
            mm.add(ModuleInfo(mid, root, "gradle"))
            by_path[(base, gpath)] = mid
            by_accessor[(base, accessor_key(gpath))] = mid
            base_of[mid] = base
            for name in BUILDS:
                if join(root, name) in fileset:
                    build_file[mid] = join(root, name)
                    break
    contents = read(list(build_file.values()))
    for mid, path in build_file.items():
        src = strip_comments(decode(contents.get(path)))
        base = base_of[mid]
        targets = {by_path.get((base, m.group(1))) for m in PROJECT_DEP.finditer(src)}
        for m in ACCESSOR_DEP.finditer(src):
            parts = m.group(1).strip(".").split(".")
            while parts and (base, ".".join(parts)) not in by_accessor:
                parts.pop()  # tolerate trailing members such as `.dependencyProject`
            if parts:
                targets.add(by_accessor[(base, ".".join(parts))])
        for target in targets - {None, mid}:
            mm.deps.add((mid, target))
        external = {f"libs.{m.group(1).strip('.')}" for m in LIBS_DEP.finditer(src)
                    if not m.group(1).startswith((".plugins.", ".versions."))}
        external |= {m.group(1) for m in COORD_DEP.finditer(src)}
        if external:
            mm.external.setdefault(mid, set()).update(external)
