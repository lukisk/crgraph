"""Language plugin registry."""
from __future__ import annotations

from pathlib import PurePosixPath

from .base import LanguagePlugin, parse_source
from .kotlin import KotlinPlugin
from .python import PythonPlugin
from .swift import SwiftPlugin
from .typescript import TypeScriptPlugin

GRAMMARS = ("kotlin", "swift", "typescript", "tsx", "javascript", "python")
BUILD_FILE_NAMES = frozenset({
    "build.gradle.kts", "settings.gradle.kts", "build.gradle", "settings.gradle", "Package.swift", "Project.swift",
})
PLUGINS: list[LanguagePlugin] = [PythonPlugin(), KotlinPlugin(), SwiftPlugin(), TypeScriptPlugin()]
_BY_SUFFIX = {ext: plugin for plugin in PLUGINS for ext in plugin.extensions}


def plugin_for(path: str) -> LanguagePlugin | None:
    p = PurePosixPath(path)
    if p.name in BUILD_FILE_NAMES or p.name.endswith(".d.ts"):
        return None
    return _BY_SUFFIX.get(p.suffix)


def source_extensions() -> list[str]:
    return sorted(_BY_SUFFIX)


__all__ = ["GRAMMARS", "LanguagePlugin", "parse_source", "plugin_for", "source_extensions"]
