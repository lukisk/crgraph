import pytest

from crgraph.gitio import INDEX, WORKTREE, FileChange, GitError, Repo


def test_three_dot_range_uses_merge_base(repo):
    c1 = repo.commit({"a.txt": "1"})
    repo.git("checkout", "-q", "-b", "feat")
    c2 = repo.commit({"b.txt": "2"})
    repo.git("checkout", "-q", "main")
    repo.commit({"c.txt": "3"})
    rng = Repo(repo.root).resolve_range(["main...feat"])
    assert (rng.base, rng.head, rng.label) == (c1, c2, "main...feat")


def test_two_dot_and_two_args(repo):
    c1 = repo.commit({"a.txt": "1"})
    c2 = repo.commit({"a.txt": "2"})
    r = Repo(repo.root)
    assert (r.resolve_range(["HEAD~1..HEAD"]).base, r.resolve_range(["HEAD~1..HEAD"]).head) == (c1, c2)
    assert (r.resolve_range([c1, c2]).base, r.resolve_range([c1, c2]).head) == (c1, c2)


def test_single_ref_is_worktree_and_staged_is_index(repo):
    c1 = repo.commit({"a.txt": "1"})
    r = Repo(repo.root)
    assert r.resolve_range([]) .head == WORKTREE
    assert r.resolve_range(["HEAD"]).base == c1
    assert r.resolve_range([], staged=True).head == INDEX


def test_unknown_revision_raises(repo):
    repo.commit({"a.txt": "1"})
    with pytest.raises(GitError, match="unknown revision: nope"):
        Repo(repo.root).resolve_range(["nope..HEAD"])


def test_changed_files_add_modify_delete_rename_with_odd_names(repo):
    base = repo.commit({"keep.py": "x = 1\n", "gone.py": "y = 1\n", "dir with space/ü.py": "z = 1\n" * 20})
    repo.mv("dir with space/ü.py", "moved/ü.py")
    repo.write({"keep.py": "x = 2\n", "new.py": "n = 1\n"})
    repo.git("rm", "-q", "gone.py")
    head = repo.commit()
    r = Repo(repo.root)
    changes = r.changed_files(r.resolve_range([base, head]))
    assert changes == sorted(changes, key=lambda c: c.path)
    assert FileChange("R", "moved/ü.py", "dir with space/ü.py").base_path == "dir with space/ü.py"
    assert {(c.status, c.path, c.old_path) for c in changes} == {
        ("D", "gone.py", None), ("M", "keep.py", None), ("R", "moved/ü.py", "dir with space/ü.py"), ("A", "new.py", None),
    }


def test_worktree_mode_includes_untracked_and_reads_disk(repo):
    repo.commit({"a.py": "a = 1\n"})
    repo.write({"a.py": "a = 2\n", "new.py": "n = 1\n"})
    r = Repo(repo.root)
    rng = r.resolve_range([])
    assert {(c.status, c.path) for c in r.changed_files(rng)} == {("M", "a.py"), ("A", "new.py")}
    assert r.read(WORKTREE, "a.py") == b"a = 2\n"
    assert "new.py" in r.list_files(WORKTREE)


def test_index_mode_reads_staged_content(repo):
    repo.commit({"a.py": "a = 1\n"})
    repo.write({"a.py": "a = 2\n"})
    repo.git("add", "a.py")
    repo.write({"a.py": "a = 3\n"})
    r = Repo(repo.root)
    rng = r.resolve_range([], staged=True)
    assert [c.path for c in r.changed_files(rng)] == ["a.py"]
    assert r.read(INDEX, "a.py") == b"a = 2\n"


def test_read_many_returns_none_for_missing(repo):
    sha = repo.commit({"a.py": "a = 1\n"})
    out = Repo(repo.root).read_many(sha, ["a.py", "missing.py"])
    assert out == {"a.py": b"a = 1\n", "missing.py": None}


def test_grep_files_and_lines_strip_ref(repo):
    sha = repo.commit({"src/a.kt": "package x.y\nclass Repo\n", "src/b.kt": "package x.z\nval r = Repository()\n"})
    r = Repo(repo.root)
    assert r.grep_files(sha, ["Repo"], ["*.kt"]) == {"src/a.kt"}
    assert sorted(r.grep_lines(sha, "^[[:space:]]*package[[:space:]]", ["*.kt"])) == [("src/a.kt", "package x.y"), ("src/b.kt", "package x.z")]


def test_discover_from_subdirectory(repo):
    repo.commit({"sub/dir/a.txt": "1"})
    assert Repo.discover(repo.root / "sub" / "dir").root.resolve() == repo.root.resolve()


def test_commit_counts(repo):
    repo.commit({"a.txt": "1"})
    repo.commit({"a.txt": "2", "b.txt": "1"})
    counts = Repo(repo.root).commit_counts("HEAD", "1 year ago")
    assert counts["a.txt"] == 2 and counts["b.txt"] == 1


def test_pathspecs_follow_git_semantics_from_a_subdirectory(repo):
    base = repo.commit({"app/sub dir/Foo.kt": "1", "app/Bar.kt": "1", "lib/Baz.py": "1"})
    head = repo.commit({"app/sub dir/Foo.kt": "2", "app/Bar.kt": "2", "lib/Baz.py": "2"})
    repo.write({"app/sub dir/New.kt": "1"})
    root = Repo.discover(repo.root)
    sub = Repo.discover(repo.root / "app" / "sub dir")
    paths = lambda r, rng, specs: [c.path for c in r.changed_files(rng, specs)]  # noqa: E731
    rng = root.resolve_range([base, head])
    assert paths(root, rng, ["*.kt"]) == ["app/Bar.kt", "app/sub dir/Foo.kt"]
    assert paths(root, rng, ["./app"]) == ["app/Bar.kt", "app/sub dir/Foo.kt"]
    assert paths(root, rng, ["."]) == ["app/Bar.kt", "app/sub dir/Foo.kt", "lib/Baz.py"]
    assert paths(sub, rng, ["Foo.kt"]) == ["app/sub dir/Foo.kt"]
    assert paths(sub, rng, ["."]) == ["app/sub dir/Foo.kt"]
    assert paths(sub, sub.resolve_range([head]), ["."]) == ["app/sub dir/New.kt"]


def test_numstat_counts_lines_per_path_with_renames_binaries_and_untracked(repo):
    base = repo.commit({"a.kt": "1\n2\n3\n", "old/b.kt": "x\n" * 20, "img.png": "p"})
    (repo.root / "img.png").write_bytes(b"\x89PNG\0\1\2")
    repo.mv("old/b.kt", "new/b.kt")
    head = repo.commit({"a.kt": "1\nTWO\n3\n4\n", "new/b.kt": "x\n" * 19 + "y\n"})
    r = Repo(repo.root)
    stat = r.numstat(r.resolve_range([base, head]))
    assert stat == {"a.kt": (2, 1), "new/b.kt": (1, 1), "img.png": None}
    assert r.numstat(r.resolve_range([base, head]), ["*.kt"]) == {"a.kt": (2, 1), "new/b.kt": (1, 1)}
    repo.write({"c dir/new.py": "a\nb\n", "a.kt": "1\n"})
    assert r.numstat(r.resolve_range([head])) == {"a.kt": (0, 3), "c dir/new.py": (2, 0)}


def test_worktree_symlink_reads_as_its_target_path(repo, tmp_path):
    secret = tmp_path / "secret.txt"
    secret.write_text("private\n")
    repo.commit({"a.py": "x = 1\n"})
    (repo.root / "link.py").symlink_to(secret)
    r = Repo(repo.root)
    assert r.read(WORKTREE, "link.py") == str(secret).encode()
    assert r.read(WORKTREE, "a.py") == b"x = 1\n"
