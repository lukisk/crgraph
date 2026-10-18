"""Shared builders for tests."""
from textwrap import dedent

from crgraph.lang import parse_source, plugin_for
from crgraph.modules import ModuleInfo, ModuleMap
from crgraph.resolve import RepoIndex


def facts_for(files: dict[str, str]):
    return {p: parse_source(plugin_for(p), p, dedent(src).lstrip("\n").encode()) for p, src in files.items()}


def module_map(roots: dict[str, str], deps=()) -> ModuleMap:
    mm = ModuleMap()
    for mid, root in roots.items():
        mm.add(ModuleInfo(mid, root, "gradle"))
    mm.deps = set(deps)
    return mm


def repo_index(facts, mm: ModuleMap) -> RepoIndex:
    lines = [(p, f"package {f.package}") for p, f in facts.items() if f.package]
    return RepoIndex(list(facts), mm, lambda: lines)


def gradle_project(modules: dict[str, list[str]]) -> dict[str, str]:
    """settings.gradle.kts + one build.gradle.kts per module: {':a': [':b']} means :a depends on :b."""
    files = {"settings.gradle.kts": "\n".join(f'include("{m}")' for m in modules) + "\n"}
    for mid, deps in modules.items():
        body = "".join(f'    implementation(project("{d}"))\n' for d in deps)
        files[f"{mid.strip(':').replace(':', '/')}/build.gradle.kts"] = "dependencies {\n" + body + "}\n"
    return files


def run_range(git_repo, base: str, head: str, config=None):
    """Full pipeline on a fixture repo."""
    from crgraph.config import Config
    from crgraph.gitio import Repo
    from crgraph.pipeline import run

    r = Repo(git_repo.root)
    return run(r, r.resolve_range([base, head]), config or Config())


def findings(result, rule: str):
    return [f for f in result.findings if f.rule == rule]
