"""Command line entry point: `crgraph <range> [-- <pathspec>...]`."""
from __future__ import annotations

import argparse
import hashlib
import os
import stat
import sys
import tempfile
import traceback
import webbrowser
from pathlib import Path

import tree_sitter_language_pack as tslp

from . import __version__
from .config import CONFIG_FILE, Config, ConfigError, apply_user_config, load_config, parse_config
from .explain import explain
from .gitio import GitError, RangeSpec, Repo
from .lang import GRAMMARS
from .model import SEVERITY_RANK, Finding, Severity
from .pipeline import run
from .render.html import to_html
from .render.jsonout import to_document, to_json
from .render.markdown import to_markdown
from .review import parse_review

FORMATS = {"html", "md", "json"}
DEFAULT_OUT = "crgraph-out"
DEFAULT_FORMATS = "html,md,json"
OUTPUT_FILES = ("delta.json", "summary.md", "report.html")


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="crgraph",
        description="Architecture-level view of a git diff: modules, files, symbols and findings. Never shows code.",
        epilog="Ranges: A..B, A...B (since merge base, like an MR), 'A B', A (A vs working tree), --staged. "
               "'crgraph grammars fetch' pre-downloads the language grammars.",
    )
    p.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    p.add_argument("revs", nargs="*", help="revision range; default: HEAD vs working tree")
    p.add_argument("--staged", action="store_true", help="compare a revision (default HEAD) with the index")
    p.add_argument("-g", "--generate", action="store_true",
                   help="write report files to --out; without it crgraph only opens report.html, "
                        "kept as one file per repository in the temp folder")
    p.add_argument("-o", "--out", help=f"output directory (default: {DEFAULT_OUT}); implies --generate")
    p.add_argument("--format", help=f"comma-separated: html, md, json (default: {DEFAULT_FORMATS}); implies --generate")
    p.add_argument("--open", action="store_true", help="with --generate: also open report.html in the browser")
    p.add_argument("--depth", choices=["module", "file", "symbol"], default="module", help="initial graph depth")
    p.add_argument("--explain", action="store_true",
                   help="add an AI architecture review (sends names, structure and findings, never code)")
    p.add_argument("--fail-on", choices=["red", "yellow", "never"], default="red", help="exit 1 at this severity")
    p.add_argument("--config", help="config file (default: .crgraph.yml as of the base revision)")
    return p


def write_output(path: Path, text: str) -> Path:
    """Never through a symlink: one planted where a report goes would make us overwrite its target."""
    try:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | getattr(os, "O_NOFOLLOW", 0), 0o644)
    except OSError as exc:
        raise ConfigError(f"cannot write {path}: {exc.strerror}{' (a symlink?)' if path.is_symlink() else ''}") from exc
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.write(text)
    return path


def base_config(repo: Repo, rng: RangeSpec) -> Config:
    """The config as of the base commit: a change must not be able to relax the rules it is reviewed with."""
    data = repo.read(rng.base, CONFIG_FILE)
    return parse_config(data.decode("utf-8", "replace"), f"{CONFIG_FILE} at {rng.base[:12]}") if data else Config()


def preview_dir() -> Path:
    """Private to the current user: on a shared /tmp another user could pre-create the folder or plant a symlink."""
    uid = os.getuid() if hasattr(os, "getuid") else None  # Windows: the temp folder is per user already
    path = Path(tempfile.gettempdir()) / (f"crgraph-{uid}" if uid is not None else "crgraph")
    path.mkdir(mode=0o700, exist_ok=True)
    info = path.lstat()
    if uid is not None and (not stat.S_ISDIR(info.st_mode) or info.st_uid != uid or info.st_mode & 0o077):
        raise ConfigError(f"{path} is not a private folder owned by you; remove it and run again")
    return path


def exit_code(findings: list[Finding], fail_on: str) -> int:
    if fail_on == "never":
        return 0
    threshold = SEVERITY_RANK[Severity.RED if fail_on == "red" else Severity.YELLOW]
    return 1 if any(SEVERITY_RANK[f.severity] >= threshold for f in findings) else 0


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv[:2] == ["grammars", "fetch"]:
        tslp.download(list(GRAMMARS))
        print(f"crgraph: grammars ready in {tslp.cache_dir()}")
        return 0
    pathspecs: list[str] = []
    if "--" in argv:
        cut = argv.index("--")
        argv, pathspecs = argv[:cut], argv[cut + 1:]
    args = build_parser().parse_args(argv)
    generate = args.generate or args.out is not None or args.format is not None
    formats = {f.strip() for f in (args.format or DEFAULT_FORMATS).split(",") if f.strip()}
    if not formats or formats - FORMATS:
        print(f"crgraph: --format must be a comma-separated subset of {', '.join(sorted(FORMATS))}", file=sys.stderr)
        return 2
    try:
        repo = Repo.discover(Path.cwd())
        rng = repo.resolve_range(args.revs, staged=args.staged)
        config = load_config(Path(args.config), required=True) if args.config else base_config(repo, rng)
        if args.explain:
            apply_user_config(config.explain)
        out = Path(args.out or DEFAULT_OUT) if generate else preview_dir()
        # our own reports from a previous run are not part of the change; only those exact files, and by their
        # unresolved path, so an output dir that is (or is linked to) real sources cannot hide changes
        out_abs = Path(os.path.abspath(out))
        if out_abs.is_relative_to(repo.root):
            rel = out_abs.relative_to(repo.root).as_posix()
            config.ignore += [name if rel == "." else f"{rel}/{name}" for name in OUTPUT_FILES]
            # the repo under review could plant `crgraph-out` as a link to somewhere we would then write into
            if os.path.realpath(out_abs) != os.path.join(os.path.realpath(repo.root), *Path(rel).parts):
                raise ConfigError(f"{out} is a symlink inside the repository; use -o with another folder")
        result = run(repo, rng, config, pathspecs)
        doc = to_document(result, depth=args.depth)
        if args.explain:
            doc["explain"], warning = explain(doc, config.explain)
            doc["review"] = parse_review(doc["explain"])
            if warning:
                doc["warnings"].append(warning)
        out.mkdir(parents=True, exist_ok=True)
        written = []
        if not generate:  # one file per repository, overwritten on every run
            formats = {"html"}
            html_path = out / f"{repo.root.name}-{hashlib.sha1(str(repo.root).encode()).hexdigest()[:8]}.html"
        else:
            html_path = out / "report.html"
        if "json" in formats:
            written.append(write_output(out / "delta.json", to_json(doc)))
        if "md" in formats:
            written.append(write_output(out / "summary.md", to_markdown(doc)))
        if "html" in formats:
            written.append(write_output(html_path, to_html(doc)))
    except (GitError, ConfigError) as exc:
        print(f"crgraph: {exc}", file=sys.stderr)
        return 2
    except tslp.DownloadError as exc:
        print(f"crgraph: could not download language grammars ({exc}). "
              "Run 'crgraph grammars fetch' once with network access.", file=sys.stderr)
        return 2
    except Exception:  # unexpected: report it, distinct from findings
        traceback.print_exc()
        return 3
    sev = doc["stats"]["findings"]
    print(f"crgraph {rng.label}: {len(result.changes)} changed path(s), findings 🔴 {sev.get('red', 0)} "
          f"🟡 {sev.get('yellow', 0)} ⚪ {sev.get('info', 0)}")
    for warning in doc["warnings"][:5]:
        print(f"  warning: {warning}")
    for path in written:
        print(f"  → {path}")
    if (args.open or not generate) and "html" in formats:
        webbrowser.open(html_path.resolve().as_uri())
    return exit_code(result.findings, args.fail_on)
