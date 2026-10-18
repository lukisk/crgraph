"""Git access: range resolution, changed files, blob reading, grep and history."""
from __future__ import annotations

import os
import stat
import subprocess
import tempfile
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

WORKTREE = "<worktree>"
INDEX = "<index>"
MAX_FILE_BYTES = 8_000_000  # above the parser limit, so oversized sources are still reported as skipped


class GitError(Exception):
    pass


def _dec(data: bytes) -> str:
    return data.decode("utf-8", "surrogateescape")


def _enc(text: str) -> bytes:
    return text.encode("utf-8", "surrogateescape")


@dataclass(frozen=True)
class RangeSpec:
    base: str  # commit sha
    head: str  # commit sha, WORKTREE or INDEX
    label: str


@dataclass(frozen=True)
class FileChange:
    status: str  # A, M, D or R
    path: str  # path at head (path at base for D)
    old_path: str | None = None  # path at base when status is R

    @property
    def base_path(self) -> str | None:
        return None if self.status == "A" else (self.old_path or self.path)

    @property
    def head_path(self) -> str | None:
        return None if self.status == "D" else self.path


class Repo:
    def __init__(self, root: Path, cwd: Path | None = None):
        self.root = root
        self.cwd = cwd or root  # where the user ran crgraph: pathspecs are relative to it, as in git

    @classmethod
    def discover(cls, cwd: Path) -> Repo:
        try:
            out = subprocess.run(["git", "rev-parse", "--show-toplevel"], cwd=cwd, check=True,
                                 capture_output=True).stdout
        except (subprocess.CalledProcessError, FileNotFoundError) as exc:
            raise GitError(f"not a git repository: {cwd}") from exc
        return cls(Path(_dec(out).strip()), cwd)

    def run(self, *args: str, input: bytes | None = None, ok_codes: tuple[int, ...] = (0,),
            cwd: Path | None = None) -> bytes:
        proc = subprocess.run(["git", "-c", "core.quotepath=false", *args], cwd=cwd or self.root, input=input,
                              capture_output=True)
        if proc.returncode not in ok_codes:
            raise GitError(f"git {' '.join(args[:3])}: {_dec(proc.stderr).strip()}")
        return proc.stdout

    def rev(self, name: str) -> str:
        try:
            return _dec(self.run("rev-parse", "--verify", "--quiet", f"{name}^{{commit}}")).strip()
        except GitError as exc:
            raise GitError(f"unknown revision: {name}") from exc

    def resolve_range(self, args: list[str], staged: bool = False) -> RangeSpec:
        if len(args) > 2:
            raise GitError("expected at most two revisions")
        if staged and (len(args) == 2 or (args and ".." in args[0])):
            raise GitError("--staged compares one revision with the index")
        if len(args) == 2:
            return RangeSpec(self.rev(args[0]), self.rev(args[1]), f"{args[0]}..{args[1]}")
        if args and "..." in args[0]:
            a, b = (x or "HEAD" for x in args[0].split("...", 1))
            base = _dec(self.run("merge-base", self.rev(a), self.rev(b))).strip()
            return RangeSpec(base, self.rev(b), f"{a}...{b}")
        if args and ".." in args[0]:
            a, b = (x or "HEAD" for x in args[0].split("..", 1))
            return RangeSpec(self.rev(a), self.rev(b), f"{a}..{b}")
        name = args[0] if args else "HEAD"
        if staged:
            return RangeSpec(self.rev(name), INDEX, f"{name}..index")
        return RangeSpec(self.rev(name), WORKTREE, f"{name}..worktree")

    def changed_files(self, rng: RangeSpec, pathspecs: list[str] | None = None) -> list[FileChange]:
        """Changed paths (repo-root relative); pathspecs are matched by git, relative to `self.cwd`."""
        limit = ["--", *pathspecs] if pathspecs else []
        cmd = ["diff", "--name-status", "-z", "-M50%"]
        if rng.head == INDEX:
            cmd += ["--cached", rng.base]
        elif rng.head == WORKTREE:
            cmd += [rng.base]
        else:
            cmd += [rng.base, rng.head]
        parts = _dec(self.run(*cmd, *limit, cwd=self.cwd)).split("\0")
        changes: list[FileChange] = []
        i = 0
        while i < len(parts) and parts[i]:
            code = parts[i]
            if code[0] in "RC":
                old, new = parts[i + 1], parts[i + 2]
                i += 3
                changes.append(FileChange("R", new, old) if code[0] == "R" else FileChange("A", new))
            else:
                changes.append(FileChange({"A": "A", "D": "D"}.get(code[0], "M"), parts[i + 1]))
                i += 2
        if rng.head == WORKTREE:
            untracked = _dec(self.run("ls-files", "-z", "--full-name", "--others", "--exclude-standard",
                                      *limit, cwd=self.cwd)).split("\0")
            changes += [FileChange("A", p) for p in untracked if p]
        return sorted(changes, key=lambda c: c.path)

    def numstat(self, rng: RangeSpec, pathspecs: list[str] | None = None) -> dict[str, tuple[int, int] | None]:
        """Lines (added, deleted) per changed path (the head path for renames); None for binary files."""
        limit = ["--", *pathspecs] if pathspecs else []
        cmd = ["diff", "--numstat", "-z", "-M50%"]
        if rng.head == INDEX:
            cmd += ["--cached", rng.base]
        elif rng.head == WORKTREE:
            cmd += [rng.base]
        else:
            cmd += [rng.base, rng.head]
        parts = _dec(self.run(*cmd, *limit, cwd=self.cwd)).split("\0")
        stats: dict[str, tuple[int, int] | None] = {}
        i = 0
        while i < len(parts) and parts[i]:
            added, deleted, path = parts[i].split("\t", 2)
            if not path:  # rename: the old and new paths follow as separate fields
                path, i = parts[i + 2], i + 3
            else:
                i += 1
            stats[path] = None if added == "-" else (int(added), int(deleted))
        if rng.head == WORKTREE:
            untracked = _dec(self.run("ls-files", "-z", "--full-name", "--others", "--exclude-standard",
                                      *limit, cwd=self.cwd)).split("\0")
            for p in filter(None, untracked):
                data = self._read_worktree(p) or b""
                stats[p] = None if b"\0" in data[:8000] else (len(data.splitlines()), 0)
        return stats

    def read_many(self, ref: str, paths: list[str]) -> dict[str, bytes | None]:
        result: dict[str, bytes | None] = {}
        if ref == WORKTREE:
            for p in paths:
                result[p] = self._read_worktree(p)
            return result
        wanted = [p for p in dict.fromkeys(paths) if "\n" not in p]
        result.update({p: None for p in paths})
        if not wanted:
            return result
        prefix = ":" if ref == INDEX else f"{ref}:"
        out = self.run("cat-file", "--batch", input=b"".join(_enc(prefix + p) + b"\n" for p in wanted))
        pos = 0
        for p in wanted:
            nl = out.index(b"\n", pos)
            header = out[pos:nl].split()
            pos = nl + 1
            if header[-1] in (b"missing", b"ambiguous"):
                continue
            size = int(header[2])
            if header[1] == b"blob":
                result[p] = out[pos:pos + size]
            pos += size + 1
        return result

    def _read_worktree(self, path: str) -> bytes | None:
        """Like git: a symlink reads as its target path, not the file behind it, so a link to ~/.ssh or /dev/zero
        in the repo reads nothing outside it. Regular files only, at most MAX_FILE_BYTES."""
        f = self.root / path
        try:
            if not f.parent.resolve().is_relative_to(self.root.resolve()):
                return None
            info = f.lstat()
            if stat.S_ISLNK(info.st_mode):
                return os.fsencode(os.readlink(f))
            if not stat.S_ISREG(info.st_mode):
                return None
            with f.open("rb") as fh:
                return fh.read(MAX_FILE_BYTES)
        except OSError:
            return None

    def read(self, ref: str, path: str) -> bytes | None:
        return self.read_many(ref, [path])[path]

    def list_files(self, ref: str) -> list[str]:
        if ref == WORKTREE:
            out = self.run("ls-files", "-z", "--cached", "--others", "--exclude-standard")
        elif ref == INDEX:
            out = self.run("ls-files", "-z")
        else:
            out = self.run("ls-tree", "-r", "-z", "--name-only", ref)
        files = {p for p in _dec(out).split("\0") if p}
        if ref == WORKTREE:
            files = {p for p in files if (self.root / p).exists()}
        return sorted(files)

    def _grep(self, ref: str, args: list[str], pathspecs: list[str]) -> str:
        cmd = ["grep", "-I", "-z", *args]
        if ref == INDEX:
            cmd.append("--cached")
        elif ref == WORKTREE:
            cmd.append("--untracked")
        else:
            cmd.append(ref)
        return _dec(self.run(*cmd, "--", *pathspecs, ok_codes=(0, 1)))

    def _strip_ref(self, ref: str, name: str) -> str:
        return name if ref in (WORKTREE, INDEX) else name.split(":", 1)[1]

    def grep_files(self, ref: str, words: list[str], pathspecs: list[str]) -> set[str]:
        """Files containing any of `words` as a whole word."""
        if not words:
            return set()
        with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False) as fh:
            fh.write("\n".join(words) + "\n")
            pattern_file = fh.name
        try:
            out = self._grep(ref, ["-l", "-w", "-F", "-f", pattern_file], pathspecs)
        finally:
            Path(pattern_file).unlink(missing_ok=True)
        return {self._strip_ref(ref, n) for n in out.split("\0") if n}

    def grep_lines(self, ref: str, regex: str, pathspecs: list[str]) -> list[tuple[str, str]]:
        """(path, line) pairs for lines matching an extended regex."""
        result = []
        for line in self._grep(ref, ["-E", "-e", regex], pathspecs).split("\n"):
            if "\0" in line:
                name, content = line.split("\0", 1)
                result.append((self._strip_ref(ref, name), content))
        return result

    def commit_counts(self, ref: str, since: str) -> Counter[str]:
        rev = "HEAD" if ref in (WORKTREE, INDEX) else ref
        out = _dec(self.run("log", f"--since={since}", "--format=", "--name-only", rev))
        return Counter(line for line in out.splitlines() if line)
