#!/usr/bin/env python3
# /// script
# requires-python = ">=3.12"
# ///
# ─── How to run ───
# cd backend && uv run python ../scripts/check_project_facts.py
# allow: SIZE_OK — this stdlib-only repository gate stays directly executable without helper drift.
"""Deterministically check tracked project facts without loading runtime configuration."""

from __future__ import annotations

import ast
import re
import subprocess
import sys
import tomllib
from collections.abc import Sequence
from pathlib import Path, PurePosixPath
from typing import Final

ROOT: Final = Path(__file__).resolve().parent.parent
ADR_FILE: Final = re.compile(r"docs/design-docs/(?:ADR|adr)-(\d{3})-[\w-]+\.md$")
ADR_LIKE_FILE: Final = re.compile(r"docs/design-docs/adr-.*\.md$", re.IGNORECASE)
ADR_ROW: Final = re.compile(r"^\|\s*(ADR-\d{3})\s*\|.*?\]\(([^)]+)\)", re.MULTILINE)
MARKDOWN_LINK: Final = re.compile(r"\[[^]]+\]\(([^)]+)\)")
VERSION_CLAUSE: Final = re.compile(r"(<=|>=|==|!=|<|>)(\d+(?:\.\d+)*)")
FACT_SURFACES: Final = ("README.md", "README_KO.md", "AGENTS.md", "docs/ARCHITECTURE.md")
CURRENT_CONTRACT_SURFACES: Final = (*FACT_SURFACES, "TASKS.md", "docs/PRD.md")
CURRENT_SOURCE_CONTRACT: Final = re.compile(
    r"<!-- project-current-source: migration=([\w-]+); deepagents=([0-9.]+); "
    r"ruff=([0-9.]+); refreshed=(\d{4}-\d{2}-\d{2}) -->"
)
RUNTIME_POLICY_ADR: Final = "docs/design-docs/adr-022-runtime-policy-lifecycle.md"
RUNTIME_POLICY_CONTRACT: Final = re.compile(
    r"<!-- runtime-policy-contract: RuntimePolicyV1; schema=1; "
    r"migration=([\w-]+); status=accepted -->"
)
E2E_CURRENT_CONTRACT: Final = re.compile(
    r"<!-- e2e-current-source: profile-personalization=(untested|covered); "
    r"refreshed=(\d{4}-\d{2}-\d{2}) -->"
)
PROFILE_E2E_SPEC: Final = "frontend/e2e/profile-personalization.spec.ts"
FUTURE_PROGRAM: Final = re.compile(r"<!-- future-program: ([a-z0-9-]+); status=([a-z]+) -->")
DEFERRED_PROGRAMS: Final = frozenset(
    {
        "rubric",
        "total-technical-debt-cleanup",
        "domain-relocation",
        "attachment-video-expansion",
        "async-subagents",
        "store-composite-backend-adoption",
        "observation-window-removal",
    }
)
STALE_PATTERNS: Final = (
    re.compile(r"deepagents[^\n]*0\.6", re.IGNORECASE),
    re.compile(r"\bm(?:59|63)(?:_[\w-]+)?\b", re.IGNORECASE),
)
CURRENT_FACT_CONTEXT: Final = re.compile(
    r"\b(?:current|latest|head|runtime|database|status|up to)\b|현재|최신|상태|까지",
    re.IGNORECASE,
)


type ProjectFacts = tuple[str, str, str, tuple[str, ...], tuple[str, ...], tuple[str, ...]]


def _text(texts: dict[PurePosixPath, str], path: str) -> str:
    if (text := texts.get(PurePosixPath(path))) is None:
        raise RuntimeError(f"required tracked metadata is missing: {path}")
    return text


def _dependency(project: str, package: str) -> str:
    try:
        parsed = tomllib.loads(project)
    except tomllib.TOMLDecodeError as error:
        raise RuntimeError(f"pyproject.toml cannot be parsed: {error}") from error
    metadata = parsed.get("project")
    if not isinstance(metadata, dict):
        raise RuntimeError("pyproject project metadata is missing")
    groups: list[object] = [metadata.get("dependencies")]
    optional = metadata.get("optional-dependencies")
    if isinstance(optional, dict):
        groups.extend(optional.values())
    prefix = re.compile(rf"^{re.escape(package)}(?=$|[<>=!~;\s\[])")
    matches = [
        item
        for group in groups
        if isinstance(group, list)
        for item in group
        if isinstance(item, str) and prefix.match(item)
    ]
    if len(matches) != 1:
        raise RuntimeError(f"pyproject must declare exactly one {package} dependency")
    return matches[0]


def _locked_version(lock: str, package: str) -> str:
    try:
        parsed = tomllib.loads(lock)
    except tomllib.TOMLDecodeError as error:
        raise RuntimeError(f"uv.lock cannot be parsed: {error}") from error
    packages = parsed.get("package")
    if not isinstance(packages, list):
        raise RuntimeError("uv.lock package metadata is missing")
    versions = [
        item["version"]
        for item in packages
        if isinstance(item, dict)
        and item.get("name") == package
        and isinstance(item.get("version"), str)
    ]
    if len(versions) != 1:
        raise RuntimeError(f"uv.lock must resolve exactly one {package} package")
    return versions[0]


def _numeric_version(value: str) -> tuple[int, ...] | None:
    if not re.fullmatch(r"\d+(?:\.\d+)*", value):
        return None
    return tuple(int(part) for part in value.split("."))


def _assert_constraint_allows(constraint: str, version: str, package: str) -> None:
    locked = _numeric_version(version)
    matches: list[re.Match[str]] = []
    for clause in constraint.removeprefix(package).split(","):
        if (match := VERSION_CLAUSE.fullmatch(clause)) is None:
            raise RuntimeError(f"{package} constraint/version is not a supported numeric range")
        matches.append(match)
    if locked is None or not matches:
        raise RuntimeError(f"{package} constraint/version is not a supported numeric range")
    allowed = True
    for match in matches:
        operator, expected_text = match.groups()
        expected = tuple(int(part) for part in expected_text.split("."))
        width = max(len(locked), len(expected))
        left = locked + (0,) * (width - len(locked))
        right = expected + (0,) * (width - len(expected))
        comparison = (left > right) - (left < right)
        allowed = (
            allowed
            and {
                "<": comparison < 0,
                "<=": comparison <= 0,
                "==": comparison == 0,
                "!=": comparison != 0,
                ">=": comparison >= 0,
                ">": comparison > 0,
            }[operator]
        )
    if not allowed:
        raise RuntimeError(
            f"pyproject {package} constraint does not allow locked version {version}"
        )


def _literal_revision(source: str, filename: str) -> tuple[str, tuple[str, ...]]:
    try:
        tree = ast.parse(source, filename=filename)
    except SyntaxError as error:
        raise RuntimeError(
            f"migration metadata cannot be parsed: {filename}: {error.msg}"
        ) from error
    values: dict[str, ast.expr] = {}
    for node in tree.body:
        match node:
            case (
                ast.Assign(targets=[ast.Name(id=name)], value=value)
                | ast.AnnAssign(target=ast.Name(id=name), value=value)
            ) if name in {"revision", "down_revision"} and value is not None:
                if name in values:
                    raise RuntimeError(
                        f"duplicate migration metadata assignment: {filename}: {name}"
                    )
                values[name] = value
    revision = values.get("revision")
    if not isinstance(revision, ast.Constant) or not isinstance(revision.value, str):
        raise RuntimeError(f"migration revision is not a string literal: {filename}")
    match values.get("down_revision"):
        case None | ast.Constant(value=None):
            return revision.value, ()
        case ast.Constant(value=str(parent)):
            return revision.value, (parent,)
        case ast.Tuple(elts=items):
            parents = tuple(
                item.value
                for item in items
                if isinstance(item, ast.Constant) and isinstance(item.value, str)
            )
            if len(parents) == len(items):
                return revision.value, parents
    raise RuntimeError(f"migration down_revision is not literal metadata: {filename}")


def derive_migration_head(migrations: dict[PurePosixPath, str]) -> str:
    nodes: dict[str, tuple[str, ...]] = {}
    for path, source in migrations.items():
        revision, parents = _literal_revision(source, str(path))
        if revision in nodes:
            raise RuntimeError(f"duplicate Alembic revision metadata: {revision}")
        nodes[revision] = parents
    parents = {parent for ancestors in nodes.values() for parent in ancestors}
    if unknown := parents - set(nodes):
        raise RuntimeError(
            f"migration metadata references missing parent(s): {', '.join(sorted(unknown))}"
        )
    heads = sorted(set(nodes) - parents)
    if len(heads) != 1:
        raise RuntimeError(
            f"migration metadata must have exactly one head, found: {', '.join(heads) or 'none'}"
        )
    visiting: set[str] = set()
    visited: set[str] = set()

    def has_cycle(revision: str) -> bool:
        if revision in visiting:
            return True
        if revision in visited:
            return False
        visiting.add(revision)
        cyclic = any(parent in nodes and has_cycle(parent) for parent in nodes[revision])
        visiting.remove(revision)
        visited.add(revision)
        return cyclic

    if any(has_cycle(revision) for revision in nodes):
        raise RuntimeError("migration metadata contains a cycle")
    reachable: set[str] = set()
    pending = [heads[0]]
    while pending:
        current = pending.pop()
        if current not in reachable:
            reachable.add(current)
            pending.extend(nodes[current])
    if reachable != set(nodes):
        raise RuntimeError("migration metadata contains disconnected revisions or cycles")
    return heads[0]


def _section(index: str, heading: str) -> tuple[str, ...]:
    if (match := re.search(rf"^## {re.escape(heading)}\s*$", index, re.MULTILINE)) is None:
        raise RuntimeError(f"execution-plan index is missing ## {heading}")
    following = re.search(r"^## ", index[match.end() :], re.MULTILINE)
    end = match.end() + following.start() if following else len(index)
    return tuple(MARKDOWN_LINK.findall(index[match.end() : end]))


def validate_indexes(
    texts: dict[PurePosixPath, str], tracked: frozenset[PurePosixPath]
) -> tuple[tuple[str, ...], tuple[str, ...], tuple[str, ...]]:
    adr_paths = [path for path in tracked if ADR_FILE.fullmatch(str(path))]
    invalid_adrs = [
        path for path in tracked if ADR_LIKE_FILE.fullmatch(str(path)) and path not in adr_paths
    ]
    if invalid_adrs:
        raise RuntimeError("tracked ADR filenames must use ADR-NNN-title.md")
    expected_adr_rows = [(f"ADR-{path.name[4:7]}", path.name) for path in adr_paths]
    expected_adrs = dict(expected_adr_rows)
    if len(expected_adrs) != len(expected_adr_rows):
        raise RuntimeError("tracked ADR files must not reuse an ADR number")
    rows = ADR_ROW.findall(_text(texts, "docs/design-docs/index.md"))
    if len(dict(rows)) != len(rows) or dict(rows) != expected_adrs:
        raise RuntimeError("ADR index must exactly link every tracked ADR file once")
    plan_candidates = [
        path
        for path in tracked
        if len(path.parts) >= 3
        and path.parts[:2] == ("docs", "exec-plans")
        and path.suffix == ".md"
        and path != PurePosixPath("docs/exec-plans/index.md")
    ]
    plans = [
        path
        for path in plan_candidates
        if len(path.parts) == 4 and path.parts[2] in {"active", "completed"}
    ]
    unclassified = sorted(set(plan_candidates) - set(plans))
    if unclassified:
        raise RuntimeError("execution-plan files must be under active/ or completed/")
    active = tuple(
        sorted(
            str(path.relative_to("docs/exec-plans")) for path in plans if path.parts[-2] == "active"
        )
    )
    completed = tuple(
        sorted(
            str(path.relative_to("docs/exec-plans"))
            for path in plans
            if path.parts[-2] == "completed"
        )
    )
    index = _text(texts, "docs/exec-plans/index.md")
    indexed_active, indexed_completed = _section(index, "Active"), _section(index, "Completed")
    if tuple(sorted(indexed_active)) != active or len(set(indexed_active)) != len(indexed_active):
        raise RuntimeError(
            "execution-plan index Active links must exactly match tracked active plans"
        )
    if tuple(sorted(indexed_completed)) != completed or len(set(indexed_completed)) != len(
        indexed_completed
    ):
        raise RuntimeError(
            "execution-plan index Completed links must exactly match tracked completed plans"
        )
    return tuple(sorted(expected_adrs.values())), active, completed


def _validate_current_facts(
    texts: dict[PurePosixPath, str],
    head: str,
    deepagents: str,
    deepagents_version: str,
    ruff: str,
    ruff_version: str,
) -> None:
    surfaces = {path: _text(texts, path) for path in FACT_SURFACES}
    for path, content in surfaces.items():
        if head not in content:
            raise RuntimeError(f"current fact is stale or missing migration head in {path}")
        for line in content.splitlines():
            if CURRENT_FACT_CONTEXT.search(line) and any(
                pattern.search(line) for pattern in STALE_PATTERNS
            ):
                raise RuntimeError(f"current fact contains a stale marker in {path}")
    if deepagents not in surfaces["docs/ARCHITECTURE.md"]:
        raise RuntimeError(
            "current fact is missing derived Deep Agents constraint in docs/ARCHITECTURE.md"
        )
    if ruff not in surfaces["AGENTS.md"]:
        raise RuntimeError("current fact is missing derived Ruff constraint in AGENTS.md")
    deepagents_markers = {
        "README.md": f"`deepagents` {deepagents_version}",
        "README_KO.md": f"`deepagents` {deepagents_version}",
        "AGENTS.md": f"**deepagents** {deepagents_version}",
        "docs/ARCHITECTURE.md": f"`{deepagents}` (lock: {deepagents_version})",
    }
    for path, marker in deepagents_markers.items():
        if marker not in surfaces[path]:
            raise RuntimeError(f"current fact is missing locked Deep Agents version in {path}")
    ruff_markers = {
        "README.md": f"Ruff {ruff_version}",
        "README_KO.md": f"Ruff {ruff_version}",
        "AGENTS.md": f"`{ruff}` (lock: {ruff_version}",
    }
    for path, marker in ruff_markers.items():
        if marker not in surfaces[path]:
            raise RuntimeError(f"current fact is missing locked Ruff version in {path}")


def _validate_document_contracts(
    texts: dict[PurePosixPath, str],
    head: str,
    deepagents_version: str,
    ruff_version: str,
) -> None:
    refresh_dates: set[str] = set()
    for path in CURRENT_CONTRACT_SURFACES:
        matches = CURRENT_SOURCE_CONTRACT.findall(_text(texts, path))
        expected_prefix = (head, deepagents_version, ruff_version)
        if len(matches) != 1 or matches[0][:3] != expected_prefix:
            raise RuntimeError(f"current-source contract is stale or missing in {path}")
        refresh_dates.add(matches[0][3])
    if len(refresh_dates) != 1:
        raise RuntimeError("current-source contract refresh dates must match")

    e2e_matches = E2E_CURRENT_CONTRACT.findall(_text(texts, "docs/e2e-coverage.md"))
    if len(e2e_matches) != 1 or e2e_matches[0][1] not in refresh_dates:
        raise RuntimeError("E2E current-source contract is stale or missing")
    if e2e_matches[0][0] == "covered":
        # Source existence is checkable here; pass/fail belongs to the E2E runner.
        _text(texts, PROFILE_E2E_SPEC)

    adr_matches = RUNTIME_POLICY_CONTRACT.findall(_text(texts, RUNTIME_POLICY_ADR))
    known_revisions = {
        _literal_revision(source, str(path))[0]
        for path, source in texts.items()
        if path.match("backend/alembic/versions/*.py")
    }
    # ADRs record the introducing migration, not the ever-changing current head.
    if len(adr_matches) != 1 or adr_matches[0] not in known_revisions:
        raise RuntimeError("ADR-022 runtime-policy contract is stale or missing")

    future_markers = FUTURE_PROGRAM.findall(_text(texts, "docs/exec-plans/index.md"))
    programs = {program for program, _status in future_markers}
    if (
        len(future_markers) != len(DEFERRED_PROGRAMS)
        or programs != DEFERRED_PROGRAMS
        or any(status != "deferred" for _program, status in future_markers)
    ):
        raise RuntimeError("deferred-program contract is stale or incomplete")


def _tracked_texts(root: Path) -> tuple[dict[PurePosixPath, str], frozenset[PurePosixPath]]:
    root = root.resolve()
    result = subprocess.run(  # noqa: S603 -- fixed git subcommand reads only this repository index
        ["git", "-C", str(root), "ls-files", "-z"],  # noqa: S607 -- git must resolve through PATH
        capture_output=True,
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError("unable to list tracked metadata with git ls-files")
    try:
        tracked_output = result.stdout.decode("utf-8")
    except UnicodeDecodeError as error:
        raise RuntimeError("tracked metadata paths are not valid UTF-8") from error
    tracked = frozenset(PurePosixPath(item) for item in tracked_output.split("\0") if item)
    fixed = frozenset(
        PurePosixPath(path)
        for path in (
            *CURRENT_CONTRACT_SURFACES,
            "docs/e2e-coverage.md",
            PROFILE_E2E_SPEC,
            "backend/pyproject.toml",
            "backend/uv.lock",
        )
    )
    allowed = {
        path
        for path in tracked
        if path in fixed
        or path.match("backend/alembic/versions/*.py")
        or path.match("docs/design-docs/*.md")
        or path == PurePosixPath("docs/exec-plans/index.md")
        or path.match("docs/exec-plans/**/*.md")
    }
    texts: dict[PurePosixPath, str] = {}
    for path in allowed:
        file = root / path
        current = root
        has_symlink_component = False
        for part in path.parts:
            current /= part
            has_symlink_component = has_symlink_component or current.is_symlink()
        try:
            resolved = file.resolve(strict=True)
        except OSError as error:
            raise RuntimeError(f"tracked metadata cannot be resolved: {path}") from error
        if has_symlink_component or not resolved.is_relative_to(root) or not resolved.is_file():
            raise RuntimeError(f"tracked metadata must be a regular file: {path}")
        try:
            texts[path] = resolved.read_text(encoding="utf-8")
        except (OSError, UnicodeError) as error:
            raise RuntimeError(f"tracked metadata is not readable UTF-8: {path}") from error
    return texts, tracked


def inspect_project_facts(repository_root: Path = ROOT) -> ProjectFacts:
    texts, tracked = _tracked_texts(repository_root)
    project, lock = _text(texts, "backend/pyproject.toml"), _text(texts, "backend/uv.lock")
    deepagents, ruff = _dependency(project, "deepagents"), _dependency(project, "ruff")
    deepagents_version, ruff_version = (
        _locked_version(lock, "deepagents"),
        _locked_version(lock, "ruff"),
    )
    _assert_constraint_allows(deepagents, deepagents_version, "deepagents")
    _assert_constraint_allows(ruff, ruff_version, "ruff")
    migrations = {
        path: source
        for path, source in texts.items()
        if path.match("backend/alembic/versions/*.py")
    }
    head = derive_migration_head(migrations)
    adr_files, active, completed = validate_indexes(texts, tracked)
    _validate_current_facts(
        texts,
        head,
        deepagents,
        deepagents_version,
        ruff,
        ruff_version,
    )
    _validate_document_contracts(texts, head, deepagents_version, ruff_version)
    return deepagents_version, ruff_version, head, adr_files, active, completed


def main(arguments: Sequence[str] | None = None) -> int:
    match tuple(sys.argv[1:] if arguments is None else arguments):
        case ():
            root = ROOT
        case ("--repo-root", value):
            root = Path(value)
        case _:
            print("usage: check_project_facts.py [--repo-root PATH]", file=sys.stderr)
            return 2
    try:
        deepagents_version, ruff_version, migration_head, _, _, _ = inspect_project_facts(root)
    except RuntimeError as error:
        print(f"Project facts check FAILED:\n{error}", file=sys.stderr)
        return 1
    print(
        f"Project facts check PASSED: deepagents={deepagents_version}, "
        f"ruff={ruff_version}, head={migration_head}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
