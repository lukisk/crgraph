import json

import pytest
from helpers import gradle_project

from crgraph import cli
from crgraph.cli import main


@pytest.fixture(autouse=True)
def opened(monkeypatch, tmp_path):
    """No real browser, and previews land in this test's temp folder."""
    urls = []
    monkeypatch.setattr(cli.webbrowser, "open", urls.append)
    monkeypatch.setattr(cli, "preview_dir", lambda: tmp_path / "preview")
    return urls


def cycle_repo(repo):
    base = repo.commit(gradle_project({":a": [":b"], ":b": []}))
    head = repo.commit(gradle_project({":a": [":b"], ":b": [":a"]}))
    return base, head


def test_red_finding_exits_1_and_writes_outputs(repo, monkeypatch, tmp_path, capsys):
    base, head = cycle_repo(repo)
    monkeypatch.chdir(repo.root)
    out = tmp_path / "out"
    assert main([f"{base}..{head}", "-o", str(out)]) == 1
    assert {p.name for p in out.iterdir()} == {"delta.json", "summary.md", "report.html"}
    assert json.loads((out / "delta.json").read_text())["findings"][0]["rule"] == "new-cycle"
    assert "🔴 1" in capsys.readouterr().out


def test_fail_on_never_and_format_subset(repo, monkeypatch, tmp_path):
    base, head = cycle_repo(repo)
    monkeypatch.chdir(repo.root)
    out = tmp_path / "out"
    assert main([base, head, "--fail-on", "never", "--format", "md", "-o", str(out)]) == 0
    assert [p.name for p in out.iterdir()] == ["summary.md"]


def test_empty_diff_is_ok(repo, monkeypatch, tmp_path):
    sha = repo.commit({"a.py": "x = 1\n"})
    monkeypatch.chdir(repo.root)
    assert main([f"{sha}..{sha}", "-o", str(tmp_path / "o")]) == 0
    assert "No findings." in (tmp_path / "o" / "summary.md").read_text()


def test_pathspec_limits_the_change(repo, monkeypatch, tmp_path):
    base, head = cycle_repo(repo)
    monkeypatch.chdir(repo.root)
    assert main([f"{base}..{head}", "-o", str(tmp_path / "o"), "--", "docs/"]) == 0


def test_usage_errors_exit_2(repo, monkeypatch, tmp_path, capsys):
    repo.commit({"a.py": "x = 1\n"})
    monkeypatch.chdir(repo.root)
    assert main(["nope..HEAD"]) == 2
    assert "unknown revision: nope" in capsys.readouterr().err
    assert main(["--format", "pdf"]) == 2
    assert main(["--config", str(tmp_path / "missing.yml")]) == 2
    repo.commit({".crgraph.yml": "bogus: 1\n"})
    assert main([]) == 2
    assert "unknown key(s): bogus" in capsys.readouterr().err


def test_not_a_repository_exits_2(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    assert main([]) == 2


def test_worktree_mode_from_subdirectory(repo, monkeypatch, tmp_path):
    repo.commit({"pkg/a.py": "def f():\n    return 1\n"})
    repo.write({"pkg/a.py": "def f():\n    return 2\n"})
    monkeypatch.chdir(repo.root / "pkg")
    assert main(["-o", str(tmp_path / "o"), "--format", "json"]) == 0
    doc = json.loads((tmp_path / "o" / "delta.json").read_text())
    assert doc["range"]["head"] == "<worktree>"
    assert any(n["id"] == "sym:pkg/a.py#f(0)" and n["status"] == "modified" for n in doc["nodes"])


def test_second_run_ignores_its_own_output(repo, monkeypatch, capsys):
    repo.commit({"a.py": "x = 1\n"})
    monkeypatch.chdir(repo.root)
    assert main([]) == 0
    capsys.readouterr()
    assert main([]) == 0
    assert ": 0 changed path(s)" in capsys.readouterr().out


def test_output_symlink_in_the_repo_is_refused(repo, monkeypatch, capsys):
    repo.commit({"src/a.py": "x = 1\n"})
    (repo.root / "crgraph-out").symlink_to("src")
    repo.commit()
    repo.write({"src/a.py": "x = 2\n"})
    monkeypatch.chdir(repo.root)
    assert main(["--format", "json"]) == 2
    assert "symlink inside the repository" in capsys.readouterr().err
    assert not (repo.root / "src" / "delta.json").exists()


def test_report_file_symlink_is_never_written_through(repo, monkeypatch, tmp_path, capsys):
    victim = tmp_path / "victim.txt"
    victim.write_text("keep\n")
    repo.commit({"a.py": "x = 1\n"})
    (repo.root / "crgraph-out").mkdir()
    (repo.root / "crgraph-out" / "summary.md").symlink_to(victim)
    repo.commit()
    monkeypatch.chdir(repo.root)
    assert main(["--format", "md"]) == 2
    assert victim.read_text() == "keep\n"
    assert "cannot write" in capsys.readouterr().err


def test_non_utf8_file_name_does_not_crash(repo, monkeypatch, tmp_path):
    import subprocess

    base = repo.commit({"a.py": "x = 1\n"})
    blob = repo.git("hash-object", "-w", "a.py")
    subprocess.run(["git", "update-index", "--add", "--index-info"], cwd=repo.root, check=True,
                   input=b"".join(b"100644 " + blob.encode() + b"\tlat" + c + b".py\n" for c in (b"\xe9", b"\xe8")))
    repo.git("commit", "-q", "-m", "latin-1 names")
    monkeypatch.chdir(repo.root)
    out = tmp_path / "o"
    assert main([f"{base}..HEAD", "-o", str(out)]) == 0
    doc = json.loads((out / "delta.json").read_text(encoding="utf-8"))
    assert {"file:lat\\xe9.py", "file:lat\\xe8.py"} <= {n["id"] for n in doc["nodes"]}  # distinct, not both U+FFFD


def test_default_only_opens_one_reused_html_and_writes_nothing_to_the_repo(repo, monkeypatch, tmp_path, opened):
    base, head = cycle_repo(repo)
    monkeypatch.chdir(repo.root)
    assert main([f"{base}..{head}"]) == 1
    assert main([f"{base}..{head}"]) == 1
    previews = list((tmp_path / "preview").iterdir())
    assert len(previews) == 1 and previews[0].suffix == ".html"
    assert opened == [previews[0].resolve().as_uri()] * 2
    assert not (repo.root / "crgraph-out").exists()


def test_generate_writes_files_without_opening(repo, monkeypatch, opened):
    base, head = cycle_repo(repo)
    monkeypatch.chdir(repo.root)
    assert main([f"{base}..{head}", "--generate", "--fail-on", "never"]) == 0
    assert {p.name for p in (repo.root / "crgraph-out").iterdir()} == {"delta.json", "summary.md", "report.html"}
    assert opened == []


def test_preview_dir_must_be_private(monkeypatch, tmp_path):
    monkeypatch.undo()  # the real preview_dir, in a temp folder of our own
    monkeypatch.setattr(cli.tempfile, "gettempdir", lambda: str(tmp_path))
    private = cli.preview_dir()
    assert private.stat().st_mode & 0o777 == 0o700
    private.chmod(0o777)
    with pytest.raises(cli.ConfigError):
        cli.preview_dir()
    private.rmdir()
    private.symlink_to(tmp_path)
    with pytest.raises(cli.ConfigError):
        cli.preview_dir()


def test_a_change_cannot_relax_its_own_config(repo, monkeypatch, tmp_path):
    base = repo.commit({"a.py": "x = 1\n"})
    head = repo.commit({"a.py": "x = 2\n", ".crgraph.yml": "ignore: ['a.py']\n"})
    monkeypatch.chdir(repo.root)
    assert main([f"{base}..{head}", "-o", str(tmp_path / "o"), "--format", "json", "--fail-on", "never"]) == 0
    doc = json.loads((tmp_path / "o" / "delta.json").read_text())
    assert any(n["id"] == "file:a.py" for n in doc["nodes"])
    assert doc["findings"][0]["rule"] == "analysis-config"
    # an explicit --config is the way to try a new one
    (tmp_path / "c.yml").write_text("ignore: ['a.py']\n")
    assert main([f"{base}..{head}", "-o", str(tmp_path / "p"), "--format", "json", "--config", str(tmp_path / "c.yml"),
                 "--fail-on", "never"]) == 0
    assert not any(n["id"] == "file:a.py" for n in json.loads((tmp_path / "p" / "delta.json").read_text())["nodes"])
