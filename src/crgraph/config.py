"""Loading and validating `.crgraph.yml`."""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

DEFAULT_IGNORE = [
    "**/build/**", "**/node_modules/**", "**/generated/**", "**/.gradle/**", "**/dist/**",
    "**/Pods/**", "**/*.min.js", "**/vendor/**",
]
DEFAULT_TEST_PATTERNS = [
    "**/*Test.kt", "**/*Tests.kt", "**/*Test.swift", "**/*Tests.swift", "**/test_*.py", "**/*_test.py",
    "**/*.spec.{ts,tsx,js,jsx,mjs,cjs,mts,cts}", "**/*.test.{ts,tsx,js,jsx,mjs,cjs,mts,cts}", "**/test/**", "**/tests/**", "**/Tests/**",
    "**/src/*Test/**", "**/__tests__/**",
]
DEFAULT_CONTRACT_FILES = [
    "**/AndroidManifest.xml", "**/Info.plist", "**/migrations/**", "**/db/migration/**", "**/*.graphql",
    "**/openapi*.{yaml,yml,json}", "**/*.proto",
]
CONFIG_FILE = ".crgraph.yml"
TOP_KEYS = {"modules", "ignore", "test_patterns", "contract_files", "rules", "thresholds", "explain",
            "fallback_depth", "blast_max_files"}


class ConfigError(Exception):
    pass


@dataclass(frozen=True)
class ForbidRule:
    src: str
    dst: str
    except_from: str | None = None

    def describe(self) -> str:
        extra = f" (except from {self.except_from})" if self.except_from else ""
        return f"forbid {self.src} -> {self.dst}{extra}"


@dataclass(frozen=True)
class LayersRule:
    order: tuple[str, ...]
    match: str

    def describe(self) -> str:
        return "layers " + " > ".join(self.order)


@dataclass
class Thresholds:
    blast_modules: int = 5
    fanout_delta: int = 3
    complexity_delta: int = 5
    complexity_max: int = 15


@dataclass
class ExplainConfig:
    model: str = "claude-opus-5-5"
    include_signatures: bool = True


@dataclass
class Config:
    modules: dict[str, str] = field(default_factory=dict)
    ignore: list[str] = field(default_factory=lambda: list(DEFAULT_IGNORE))
    test_patterns: list[str] = field(default_factory=lambda: list(DEFAULT_TEST_PATTERNS))
    contract_files: list[str] = field(default_factory=lambda: list(DEFAULT_CONTRACT_FILES))
    rules: list[ForbidRule | LayersRule] = field(default_factory=list)
    thresholds: Thresholds = field(default_factory=Thresholds)
    explain: ExplainConfig = field(default_factory=ExplainConfig)
    fallback_depth: int = 2
    blast_max_files: int = 500


def _expect(value: Any, type_: type, where: str) -> Any:
    if not isinstance(value, type_):
        raise ConfigError(f"{where} must be a {type_.__name__}")
    return value


def _str_list(value: Any, where: str) -> list[str]:
    items = _expect(value, list, where)
    if not all(isinstance(x, str) for x in items):
        raise ConfigError(f"{where} must be a list of strings")
    return items


def _rule(raw: Any, where: str) -> ForbidRule | LayersRule:
    if not isinstance(raw, dict) or len(raw) != 1:
        raise ConfigError(f"{where} must have exactly one of 'forbid' or 'layers'")
    ((kind, body),) = raw.items()
    if kind == "forbid":
        if not isinstance(body, dict) or not isinstance(body.get("from"), str) or not isinstance(body.get("to"), str):
            raise ConfigError(f"{where}.forbid needs string 'from' and 'to'")
        extra = set(body) - {"from", "to", "except_from"}
        if extra:
            raise ConfigError(f"{where}.forbid has unknown key(s): {', '.join(sorted(extra))}")
        return ForbidRule(body["from"], body["to"], body.get("except_from"))
    if kind == "layers":
        order = body.get("order") if isinstance(body, dict) else None
        match = body.get("match") if isinstance(body, dict) else None
        if not (isinstance(order, list) and len(order) >= 2 and all(isinstance(x, str) for x in order)):
            raise ConfigError(f"{where}.layers.order must be a list of at least two names")
        if not isinstance(match, str) or "{layer}" not in match:
            raise ConfigError(f"{where}.layers.match must be a glob containing {{layer}}")
        return LayersRule(tuple(order), match)
    raise ConfigError(f"{where}: unknown rule type '{kind}'")


def load_config(path: Path | None, *, required: bool = False) -> Config:
    if path is None or not path.exists():
        if required:
            raise ConfigError(f"config file not found: {path}")
        return Config()
    return parse_config(path.read_text(), str(path))


def parse_config(text: str, path: str) -> Config:
    """`path` only names the source in error messages."""
    try:
        raw = yaml.safe_load(text) or {}
    except yaml.YAMLError as exc:
        raise ConfigError(f"{path}: invalid YAML: {exc}") from exc
    if not isinstance(raw, dict):
        raise ConfigError(f"{path}: top level must be a mapping")
    unknown = set(raw) - TOP_KEYS
    if unknown:
        raise ConfigError(f"{path}: unknown key(s): {', '.join(sorted(unknown))}")
    cfg = Config()
    modules = _expect(raw.get("modules", {}), dict, f"{path}: modules")
    if not all(isinstance(k, str) and isinstance(v, str) for k, v in modules.items()):
        raise ConfigError(f"{path}: modules must map glob strings to module id strings")
    cfg.modules = modules
    cfg.ignore += _str_list(raw.get("ignore", []), f"{path}: ignore")
    cfg.test_patterns += _str_list(raw.get("test_patterns", []), f"{path}: test_patterns")
    cfg.contract_files += _str_list(raw.get("contract_files", []), f"{path}: contract_files")
    rules = _expect(raw.get("rules", []), list, f"{path}: rules")
    cfg.rules = [_rule(r, f"{path}: rules[{i}]") for i, r in enumerate(rules)]
    for key, value in _expect(raw.get("thresholds", {}), dict, f"{path}: thresholds").items():
        if not hasattr(cfg.thresholds, key) or not isinstance(value, int):
            raise ConfigError(f"{path}: thresholds.{key} must be a known integer setting")
        setattr(cfg.thresholds, key, value)
    explain = _expect(raw.get("explain", {}), dict, f"{path}: explain")
    if "model" in explain:
        cfg.explain.model = _expect(explain["model"], str, f"{path}: explain.model")
    if "include_signatures" in explain:
        cfg.explain.include_signatures = _expect(explain["include_signatures"], bool, f"{path}: explain.include_signatures")
    for key in ("fallback_depth", "blast_max_files"):
        if key in raw:
            setattr(cfg, key, _expect(raw[key], int, f"{path}: {key}"))
    return cfg
