"""Loading and validating `.crgraph.yml` and the personal config (`~/.config/crgraph/config.yml`)."""
from __future__ import annotations

import os
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
EXPLAIN_KEYS = {"model", "include_signatures", "plain"}
# where the summary goes and with which credentials: personal config only, so a repository cannot redirect them
PERSONAL_EXPLAIN_KEYS = {"base_url", "auth_token", "auth_token_env", "auth_token_command", "token_header"}
TOKEN_SOURCES = ("auth_token", "auth_token_env", "auth_token_command")
TOKEN_HEADERS = ("authorization", "x-api-key")


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
    plain: bool = False  # only standard Messages API fields: for proxies and non-Claude models
    base_url: str | None = None
    auth_token: str | None = field(default=None, repr=False)
    auth_token_env: str | None = None  # name of the variable holding the token
    auth_token_command: str | None = None  # shell command printing the token
    token_header: str = "authorization"  # or "x-api-key"


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


def _explain(raw: Any, cfg: ExplainConfig, where: str, allowed: set[str]) -> None:
    explain = _expect(raw, dict, where)
    personal = set(explain) & PERSONAL_EXPLAIN_KEYS - allowed
    if personal:
        raise ConfigError(f"{where}: {', '.join(sorted(personal))} can only be set in your personal config "
                          f"({user_config_path()})")
    unknown = set(explain) - allowed
    if unknown:
        raise ConfigError(f"{where}: unknown key(s): {', '.join(sorted(unknown))}")
    for key, value in explain.items():
        setattr(cfg, key, _expect(value, bool if key in ("include_signatures", "plain") else str, f"{where}.{key}"))
    if cfg.token_header not in TOKEN_HEADERS:
        raise ConfigError(f"{where}.token_header must be one of: {', '.join(TOKEN_HEADERS)}")
    if sum(1 for key in TOKEN_SOURCES if getattr(cfg, key)) > 1:
        raise ConfigError(f"{where}: set only one of {', '.join(TOKEN_SOURCES)}")


def _load_yaml(text: str, path: str) -> dict[str, Any]:
    try:
        raw = yaml.safe_load(text) or {}
    except yaml.YAMLError as exc:
        raise ConfigError(f"{path}: invalid YAML: {exc}") from exc
    if not isinstance(raw, dict):
        raise ConfigError(f"{path}: top level must be a mapping")
    return raw


def user_config_path() -> Path:
    return Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config") / "crgraph" / "config.yml"


def apply_user_config(cfg: ExplainConfig, path: Path | None = None) -> None:
    """Personal settings override the repository's `explain` settings."""
    path = path or user_config_path()
    if not path.is_file():
        return
    raw = _load_yaml(path.read_text(), str(path))
    unknown = set(raw) - {"explain"}
    if unknown:
        raise ConfigError(f"{path}: unknown key(s): {', '.join(sorted(unknown))} (only explain is personal)")
    explain = raw.get("explain", {})
    if isinstance(explain, dict) and "auth_token" in explain and os.name == "posix" and path.stat().st_mode & 0o077:
        raise ConfigError(f"{path} holds a token but other users can read it; run: chmod 600 {path}")
    _explain(explain, cfg, f"{path}: explain", EXPLAIN_KEYS | PERSONAL_EXPLAIN_KEYS)


def load_config(path: Path | None, *, required: bool = False) -> Config:
    if path is None or not path.exists():
        if required:
            raise ConfigError(f"config file not found: {path}")
        return Config()
    return parse_config(path.read_text(), str(path))


def parse_config(text: str, path: str) -> Config:
    """`path` only names the source in error messages."""
    raw = _load_yaml(text, path)
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
    _explain(raw.get("explain", {}), cfg.explain, f"{path}: explain", EXPLAIN_KEYS)
    for key in ("fallback_depth", "blast_max_files"):
        if key in raw:
            setattr(cfg, key, _expect(raw[key], int, f"{path}: {key}"))
    return cfg
