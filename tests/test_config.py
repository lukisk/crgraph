import pytest

from crgraph.config import (
    DEFAULT_IGNORE,
    ConfigError,
    ExplainConfig,
    ForbidRule,
    LayersRule,
    apply_user_config,
    load_config,
)
from crgraph.globs import glob_match, literal_prefix, match_any


def test_glob_double_star_and_braces():
    assert glob_match("**/build/**", "a/build/x.kt")
    assert glob_match("**/build/**", "build/x.kt")
    assert not glob_match("*.kt", "a/b.kt")
    assert glob_match("**/*.kt", "b.kt")
    assert glob_match("**/*.spec.{ts,tsx}", "web/a.spec.tsx")
    assert glob_match("**:impl", ":platform:core:impl")
    assert glob_match("platform/**", "platform/domain")
    assert match_any(["x", "**/y/**"], "a/y/b")


def test_literal_prefix():
    assert literal_prefix("app/src/**/domain/**") == "app/src"
    assert literal_prefix("shared/multiplatform/**") == "shared/multiplatform"
    assert literal_prefix("shared/core") == "shared/core"


def test_missing_file_gives_defaults(tmp_path):
    cfg = load_config(tmp_path / ".crgraph.yml")
    assert cfg.ignore == DEFAULT_IGNORE and cfg.rules == [] and cfg.thresholds.blast_modules == 5
    assert cfg.explain.model == "claude-opus-5-5"


def test_required_missing_file_raises(tmp_path):
    with pytest.raises(ConfigError, match="not found"):
        load_config(tmp_path / "nope.yml", required=True)


def test_full_config(tmp_path):
    p = tmp_path / ".crgraph.yml"
    p.write_text(
        """
modules:
  "shared/multiplatform/**": legacy-shared
ignore: ["**/*.pb.kt"]
rules:
  - forbid: { from: "platform/**", to: "shared/multiplatform/**" }
  - forbid: { from: "**", to: "**:impl", except_from: "**/di/**" }
  - layers: { order: [ui, presentation, domain, data], match: "**/{layer}/**" }
thresholds: { blast_modules: 9 }
explain: { include_signatures: false }
"""
    )
    cfg = load_config(p)
    assert cfg.modules == {"shared/multiplatform/**": "legacy-shared"}
    assert "**/*.pb.kt" in cfg.ignore and "**/build/**" in cfg.ignore
    assert cfg.rules[0] == ForbidRule("platform/**", "shared/multiplatform/**")
    assert cfg.rules[1].except_from == "**/di/**"
    assert cfg.rules[2] == LayersRule(("ui", "presentation", "domain", "data"), "**/{layer}/**")
    assert cfg.thresholds.blast_modules == 9 and cfg.thresholds.fanout_delta == 3
    assert cfg.explain.include_signatures is False


@pytest.mark.parametrize(
    "body, message",
    [
        ("colour: red\n", "unknown key\\(s\\): colour"),
        ("rules:\n  - forbid: { from: a }\n", "rules\\[0\\].forbid needs string 'from' and 'to'"),
        ("rules:\n  - layers: { order: [a, b], match: '**/x/**' }\n", "must be a glob containing \\{layer\\}"),
        ("rules:\n  - allow: {}\n", "unknown rule type 'allow'"),
        ("thresholds: { speed: 3 }\n", "thresholds.speed"),
        ("rules: [\n", "invalid YAML"),
    ],
)
def test_invalid_config_names_path_and_key(tmp_path, body, message):
    p = tmp_path / ".crgraph.yml"
    p.write_text(body)
    with pytest.raises(ConfigError, match=message) as err:
        load_config(p)
    assert str(p) in str(err.value)


def test_repository_config_cannot_redirect_the_summary(tmp_path):
    p = tmp_path / ".crgraph.yml"
    p.write_text("explain: { base_url: https://evil.example, auth_token_env: HOME }\n")
    with pytest.raises(ConfigError, match="auth_token_env, base_url can only be set in your personal config"):
        load_config(p)
    p.write_text("explain: { modle: x }\n")
    with pytest.raises(ConfigError, match="unknown key"):
        load_config(p)
    p.write_text("explain: { model: gpt-proxy, plain: true }\n")
    assert load_config(p).explain.plain is True


def test_personal_config_overrides_the_repository(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    repo = tmp_path / ".crgraph.yml"
    repo.write_text("explain: { model: claude-haiku-5-5, include_signatures: false }\n")
    cfg = load_config(repo).explain
    apply_user_config(cfg)  # no personal file yet: unchanged
    assert cfg.model == "claude-haiku-5-5" and cfg.base_url is None
    user = tmp_path / "crgraph" / "config.yml"
    user.parent.mkdir()
    user.write_text("explain:\n  model: proxy-model\n  base_url: https://proxy.example\n"
                    "  auth_token_env: LLM_PROXY_TOKEN\n  plain: true\n")
    apply_user_config(cfg)
    assert (cfg.model, cfg.base_url, cfg.auth_token_env, cfg.plain, cfg.include_signatures) == \
        ("proxy-model", "https://proxy.example", "LLM_PROXY_TOKEN", True, False)


@pytest.mark.parametrize(("text", "error"), [
    ("rules: []\n", "only explain is personal"),
    ("explain: { auth_token_env: A, auth_token_command: b }\n", "only one of"),
    ("explain: { token_header: cookie }\n", "token_header must be one of"),
    ("explain: { plain: yes-please }\n", "explain.plain must be a bool"),
])
def test_personal_config_is_validated(tmp_path, text, error):
    user = tmp_path / "config.yml"
    user.write_text(text)
    with pytest.raises(ConfigError, match=error):
        apply_user_config(ExplainConfig(), user)


def test_a_token_in_the_personal_config_needs_a_private_file(tmp_path):
    user = tmp_path / "config.yml"
    user.write_text("explain: { base_url: https://proxy.example, auth_token: tok-file }\n")
    user.chmod(0o644)
    with pytest.raises(ConfigError, match="chmod 600"):
        apply_user_config(ExplainConfig(), user)
    user.chmod(0o600)
    cfg = ExplainConfig()
    apply_user_config(cfg, user)
    assert cfg.auth_token == "tok-file" and "tok-file" not in repr(cfg)
