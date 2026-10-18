import pytest

from crgraph.config import DEFAULT_IGNORE, ConfigError, ForbidRule, LayersRule, load_config
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
