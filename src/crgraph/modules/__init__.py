"""Module detection across build systems."""
from __future__ import annotations

from ..config import Config
from ..globs import match_any
from . import golang, gradle, npm, php, python, swift
from .model import ModuleInfo, ModuleMap, Reader

DETECTORS = (gradle.detect, swift.detect, npm.detect, python.detect, golang.detect, php.detect)


def detect_modules(files: list[str], read: Reader, config: Config) -> ModuleMap:
    mm = ModuleMap(overrides=dict(config.modules), fallback_depth=config.fallback_depth)
    files = [f for f in files if not match_any(config.ignore, f)]
    for detector in DETECTORS:
        detector(files, read, mm)
    return mm


__all__ = ["ModuleInfo", "ModuleMap", "Reader", "detect_modules"]
