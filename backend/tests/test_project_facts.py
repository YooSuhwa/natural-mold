"""Contract tests for the deterministic repository-facts checker."""
# allow: SIZE_OK — this fixture-driven mutation matrix stays co-located for auditability.

from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path
from types import ModuleType

import pytest

SCRIPT_PATH = Path(__file__).resolve().parents[2] / "scripts" / "check_project_facts.py"


def _load_checker() -> ModuleType:
    spec = importlib.util.spec_from_file_location("project_facts_checker", SCRIPT_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _write_fixture_repository(repository: Path) -> None:
    files = {
        "backend/pyproject.toml": """[project]
dependencies = [\"deepagents>=0.7.11,<0.8.0\"]

[project.optional-dependencies]
dev = [\"ruff>=0.16.5,<0.17.0\"]
""",
        "backend/uv.lock": """version = 1

[[package]]
name = \"deepagents\"
version = \"0.7.11\"

[[package]]
name = \"ruff\"
version = \"0.16.5\"
""",
        "backend/alembic/versions/m69_previous.py": """revision = \"m69_previous\"
down_revision = None
""",
        "backend/alembic/versions/m70_current.py": """revision: str = \"m70_current\"
down_revision: str | None = \"m69_previous\"
""",
        "backend/alembic/versions/m71_current.py": """revision: str = \"m71_current\"
down_revision: str | None = \"m70_current\"
""",
        "docs/design-docs/adr-020-example.md": "# ADR-020\n",
        "docs/design-docs/adr-021-example.md": "# ADR-021\n",
        "docs/design-docs/adr-022-runtime-policy-lifecycle.md": (
            "<!-- runtime-policy-contract: RuntimePolicyV1; schema=1; "
            "migration=m71_current; status=accepted -->\n# ADR-022\n"
        ),
        "docs/design-docs/index.md": """# ADR Index

| ADR | Title |
| --- | --- |
| ADR-020 | [Example](adr-020-example.md) |
| ADR-021 | [Example](adr-021-example.md) |
| ADR-022 | [Runtime policy](adr-022-runtime-policy-lifecycle.md) |
""",
        "docs/exec-plans/active/active-plan.md": "# Active plan\n",
        "docs/exec-plans/completed/completed-plan.md": "# Completed plan\n",
        "docs/exec-plans/index.md": """# Execution Plans Index

## Active

- [Active](active/active-plan.md)

## Completed

- [Completed](completed/completed-plan.md)

## Deferred programs

<!-- future-program: rubric; status=deferred -->
<!-- future-program: total-technical-debt-cleanup; status=deferred -->
<!-- future-program: domain-relocation; status=deferred -->
<!-- future-program: attachment-video-expansion; status=deferred -->
<!-- future-program: async-subagents; status=deferred -->
<!-- future-program: store-composite-backend-adoption; status=deferred -->
<!-- future-program: observation-window-removal; status=deferred -->
""",
        "README.md": (
            "<!-- project-current-source: migration=m71_current; deepagents=0.7.11; "
            "ruff=0.16.5; refreshed=2026-09-05 -->\n"
            "AI runtime: `deepagents` 0.7.11; Ruff 0.16.5; Current migration: `m71_current`\n"
        ),
        "README_KO.md": (
            "<!-- project-current-source: migration=m71_current; deepagents=0.7.11; "
            "ruff=0.16.5; refreshed=2026-09-05 -->\n"
            "AI runtime: `deepagents` 0.7.11; Ruff 0.16.5; 현재 migration: `m71_current`\n"
        ),
        "AGENTS.md": (
            "<!-- project-current-source: migration=m71_current; deepagents=0.7.11; "
            "ruff=0.16.5; refreshed=2026-09-05 -->\n"
            "AI Runtime: **deepagents** 0.7.11; Ruff `ruff>=0.16.5,<0.17.0` "
            "(lock: 0.16.5); migration: `m71_current`\n"
        ),
        "docs/ARCHITECTURE.md": (
            "<!-- project-current-source: migration=m71_current; deepagents=0.7.11; "
            "ruff=0.16.5; refreshed=2026-09-05 -->\n"
            "Runtime: `deepagents>=0.7.11,<0.8.0` (lock: 0.7.11); migration: `m71_current`\n"
        ),
        "TASKS.md": (
            "<!-- project-current-source: migration=m71_current; deepagents=0.7.11; "
            "ruff=0.16.5; refreshed=2026-09-05 -->\n"
            "Current head: `m71_current`; historical M59 feature origin.\n"
        ),
        "docs/PRD.md": (
            "<!-- project-current-source: migration=m71_current; deepagents=0.7.11; "
            "ruff=0.16.5; refreshed=2026-09-05 -->\n"
            "Current head: `m71_current`; historical M59 feature origin.\n"
        ),
        "docs/e2e-coverage.md": (
            "<!-- e2e-current-source: profile-personalization=untested; refreshed=2026-09-05 -->\n"
        ),
    }
    for relative_path, content in files.items():
        path = repository / relative_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")

    for command in (
        ["/usr/bin/git", "init", "--quiet"],
        ["/usr/bin/git", "add", "."],
    ):
        subprocess.run(command, cwd=repository, check=True)


@pytest.fixture
def fixture_repository(tmp_path: Path) -> Path:
    # Given a tracked metadata-only repository with complete canonical facts.
    _write_fixture_repository(tmp_path)
    return tmp_path


def test_inspect_project_facts_returns_derived_canonical_values_when_complete(
    fixture_repository: Path,
) -> None:
    # Given complete, internally consistent tracked project metadata.
    checker = _load_checker()

    # When the checker derives the repository facts.
    facts = checker.inspect_project_facts(fixture_repository)

    # Then resolved versions, head, and exact index entries are observable.
    assert facts[0] == "0.7.11"
    assert facts[1] == "0.16.5"
    assert facts[2] == "m71_current"
    assert facts[3] == (
        "adr-020-example.md",
        "adr-021-example.md",
        "adr-022-runtime-policy-lifecycle.md",
    )
    assert facts[4] == ("active/active-plan.md",)
    assert facts[5] == ("completed/completed-plan.md",)


@pytest.mark.parametrize(
    ("path", "old", "new", "expected_error"),
    [
        ("README.md", "`deepagents` 0.7.11", "`deepagents` 0.7.10", "locked Deep Agents"),
        ("README_KO.md", "`deepagents` 0.7.11", "`deepagents` 0.7.10", "locked Deep Agents"),
        ("AGENTS.md", "**deepagents** 0.7.11", "**deepagents** 0.7.10", "locked Deep Agents"),
        (
            "docs/ARCHITECTURE.md",
            "(lock: 0.7.11)",
            "(lock: 0.7.10)",
            "locked Deep Agents",
        ),
        ("README.md", "Ruff 0.16.5", "Ruff 0.16.4", "locked Ruff"),
        ("README_KO.md", "Ruff 0.16.5", "Ruff 0.16.4", "locked Ruff"),
        ("AGENTS.md", "(lock: 0.16.5)", "(lock: 0.16.4)", "locked Ruff"),
    ],
)
def test_inspect_project_facts_rejects_wrong_displayed_locked_versions(
    fixture_repository: Path,
    path: str,
    old: str,
    new: str,
    expected_error: str,
) -> None:
    # Given one canonical current-state marker drifts from the lock-derived version.
    checker = _load_checker()
    surface = fixture_repository / path
    content = surface.read_text(encoding="utf-8")
    assert content.count(old) == 1
    surface.write_text(content.replace(old, new), encoding="utf-8")

    # When the source facts are checked, the displayed drift is rejected.
    with pytest.raises(RuntimeError, match=expected_error):
        checker.inspect_project_facts(fixture_repository)


@pytest.mark.parametrize(
    ("path", "replacement", "expected_error"),
    [
        (
            "README.md",
            "Current migration: `m69_previous`\n",
            "current fact",
        ),
        (
            "README.md",
            "Current AI runtime: DeepAgents 0.6.9; migration `m71_current`\n",
            "stale marker",
        ),
        (
            "README_KO.md",
            "현재 migration head: M59 / M63; derived head `m71_current`\n",
            "stale marker",
        ),
        (
            "docs/design-docs/index.md",
            (
                "# ADR Index\n\n| ADR | Title |\n| --- | --- |\n"
                "| ADR-020 | [Example](adr-020-example.md) |\n"
            ),
            "ADR index",
        ),
        (
            "docs/design-docs/index.md",
            """# ADR Index

| ADR | Title |
| --- | --- |
| ADR-020 | [Example](adr-020-example.md) |
| ADR-021 | [Example](adr-021-example.md) |
| ADR-999 | [Unexpected](adr-999-not-a-file.md) |
""",
            "ADR index",
        ),
        (
            "docs/exec-plans/index.md",
            """# Execution Plans Index

## Active

## Completed

- [Completed](completed/completed-plan.md)
""",
            "Active links",
        ),
        (
            "docs/exec-plans/index.md",
            """# Execution Plans Index

## Active

- [Active](active/active-plan.md)

## Completed

- [Completed](completed/completed-plan.md)
- [Unexpected](completed/not-a-plan.md)
""",
            "execution-plan index",
        ),
        (
            "docs/exec-plans/index.md",
            """# Execution Plans Index

## Active

- [Completed in wrong section](completed/completed-plan.md)

## Completed

- [Active in wrong section](active/active-plan.md)
""",
            "Active links",
        ),
    ],
)
def test_inspect_project_facts_rejects_stale_missing_and_extra_fixtures(
    fixture_repository: Path,
    path: str,
    replacement: str,
    expected_error: str,
) -> None:
    # Given a tracked repository whose metadata fixture has one deterministic defect.
    checker = _load_checker()
    (fixture_repository / path).write_text(replacement, encoding="utf-8")

    # When the checker reads the working tree version of that tracked metadata.
    with pytest.raises(RuntimeError, match=expected_error):
        checker.inspect_project_facts(fixture_repository)


@pytest.mark.parametrize(
    ("path", "replacement", "expected_error"),
    [
        (
            "backend/pyproject.toml",
            """# \"deepagents>=0.7.11,<0.8.0\" is only a comment decoy.
[project]
dependencies = [\"deepagents>=0.8.0,<0.9.0\"]

[project.optional-dependencies]
dev = [\"ruff>=0.16.5,<0.17.0\"]
""",
            "deepagents constraint does not allow locked version",
        ),
        (
            "backend/pyproject.toml",
            """[project]
dependencies = [\"deepagents>=0.7.11,<0.8.0\"]

[project.optional-dependencies]
dev = [\"ruff>=0.17.0,<0.18.0\"]
""",
            "ruff constraint does not allow locked version",
        ),
        (
            "backend/uv.lock",
            """version = 1

[[package]]
name = \"deepagents\"
version = \"0.8.0\"

[[package]]
name = \"ruff\"
version = \"0.16.5\"
""",
            "deepagents constraint does not allow locked version",
        ),
    ],
)
def test_inspect_project_facts_rejects_manifest_lock_version_mismatch(
    fixture_repository: Path, path: str, replacement: str, expected_error: str
) -> None:
    # Given a manifest or lock that selects incompatible Deep Agents metadata.
    checker = _load_checker()
    (fixture_repository / path).write_text(replacement, encoding="utf-8")

    # When the source-derived facts are checked.
    with pytest.raises(RuntimeError, match=expected_error):
        checker.inspect_project_facts(fixture_repository)

    # Then a version mismatch is an explicit deterministic failure.


def test_inspect_project_facts_rejects_disconnected_migration_head(
    fixture_repository: Path,
) -> None:
    # Given a second tracked migration graph with no parent relationship.
    checker = _load_checker()
    extra = fixture_repository / "backend/alembic/versions/m70_disconnected.py"
    extra.write_text('revision = "m70_disconnected"\ndown_revision = None\n', encoding="utf-8")
    subprocess.run(
        ["/usr/bin/git", "add", str(extra.relative_to(fixture_repository))],
        cwd=fixture_repository,
        check=True,
    )

    # When migration metadata is derived without importing the modules.
    with pytest.raises(RuntimeError, match="exactly one head"):
        checker.inspect_project_facts(fixture_repository)

    # Then disconnected heads cannot be represented as a current migration fact.


def test_inspect_project_facts_rejects_orphaned_migration_cycle(fixture_repository: Path) -> None:
    # Given two tracked migrations that form a cycle outside the visible head chain.
    checker = _load_checker()
    first = fixture_repository / "backend/alembic/versions/m66_cycle.py"
    second = fixture_repository / "backend/alembic/versions/m67_cycle.py"
    first.write_text('revision = "m66_cycle"\ndown_revision = "m67_cycle"\n', encoding="utf-8")
    second.write_text('revision = "m67_cycle"\ndown_revision = "m66_cycle"\n', encoding="utf-8")
    subprocess.run(
        [
            "/usr/bin/git",
            "add",
            str(first.relative_to(fixture_repository)),
            str(second.relative_to(fixture_repository)),
        ],
        cwd=fixture_repository,
        check=True,
    )

    # When the static migration graph is derived.
    with pytest.raises(RuntimeError, match="cycle"):
        checker.inspect_project_facts(fixture_repository)

    # Then one visible head cannot hide an orphaned cycle.


def test_inspect_project_facts_rejects_cycle_reachable_from_visible_head(
    fixture_repository: Path,
) -> None:
    # Given the visible head leads into a parent cycle.
    checker = _load_checker()
    previous = fixture_repository / "backend/alembic/versions/m69_previous.py"
    previous.write_text(
        'revision = "m69_previous"\ndown_revision = "m70_current"\n', encoding="utf-8"
    )

    # When the graph is checked, the reachable cycle is still rejected.
    with pytest.raises(RuntimeError, match="cycle"):
        checker.inspect_project_facts(fixture_repository)


def test_inspect_project_facts_rejects_duplicate_tracked_adr_number(
    fixture_repository: Path,
) -> None:
    # Given a second tracked file reuses ADR-020 and the index links only that duplicate.
    checker = _load_checker()
    duplicate = fixture_repository / "docs/design-docs/adr-020-duplicate.md"
    duplicate.write_text("# ADR-020 duplicate\n", encoding="utf-8")
    index = fixture_repository / "docs/design-docs/index.md"
    index.write_text(
        index.read_text(encoding="utf-8").replace("adr-020-example.md", duplicate.name),
        encoding="utf-8",
    )
    subprocess.run(
        ["/usr/bin/git", "add", str(duplicate.relative_to(fixture_repository))],
        cwd=fixture_repository,
        check=True,
    )

    # When the exact ADR set is derived, duplicate identifiers fail closed.
    with pytest.raises(RuntimeError, match="reuse an ADR number"):
        checker.inspect_project_facts(fixture_repository)


def test_inspect_project_facts_rejects_malformed_tracked_adr_filename(
    fixture_repository: Path,
) -> None:
    # Given an ADR-like tracked file does not use the three-digit identifier contract.
    checker = _load_checker()
    invalid = fixture_repository / "docs/design-docs/ADR-22-invalid.md"
    invalid.write_text("# Invalid ADR filename\n", encoding="utf-8")
    subprocess.run(
        ["/usr/bin/git", "add", str(invalid.relative_to(fixture_repository))],
        cwd=fixture_repository,
        check=True,
    )

    # When the exact ADR inventory is checked, malformed identifiers fail closed.
    with pytest.raises(RuntimeError, match="ADR filenames"):
        checker.inspect_project_facts(fixture_repository)


def test_inspect_project_facts_rejects_plan_outside_classified_directories(
    fixture_repository: Path,
) -> None:
    # Given a tracked plan is placed outside active/ and completed/.
    checker = _load_checker()
    draft = fixture_repository / "docs/exec-plans/draft/draft-plan.md"
    draft.parent.mkdir(parents=True)
    draft.write_text("# Draft\n", encoding="utf-8")
    subprocess.run(
        ["/usr/bin/git", "add", str(draft.relative_to(fixture_repository))],
        cwd=fixture_repository,
        check=True,
    )

    # When the plan inventory is checked, the unclassified directory is rejected.
    with pytest.raises(RuntimeError, match="active/ or completed/"):
        checker.inspect_project_facts(fixture_repository)


def test_inspect_project_facts_rejects_symlinked_metadata_parent(
    fixture_repository: Path,
) -> None:
    # Given a tracked metadata directory is replaced by a symlink outside the repository.
    checker = _load_checker()
    metadata = fixture_repository / "docs/design-docs"
    outside = fixture_repository.parent / "outside-design-docs"
    metadata.rename(outside)
    metadata.symlink_to(outside, target_is_directory=True)

    # When tracked metadata is read, no symlink component may cross the repository boundary.
    with pytest.raises(RuntimeError, match="regular file"):
        checker.inspect_project_facts(fixture_repository)


def test_inspect_project_facts_ignores_untracked_metadata(fixture_repository: Path) -> None:
    # Given an extra ADR-shaped file that is deliberately not tracked by git.
    checker = _load_checker()
    untracked = fixture_repository / "docs/design-docs/adr-023-untracked.md"
    untracked.write_text("# ADR-023\n", encoding="utf-8")

    # When the checker reads its tracked metadata boundary.
    facts = checker.inspect_project_facts(fixture_repository)

    # Then the untracked file cannot influence canonical source-derived facts.
    assert facts[3] == (
        "adr-020-example.md",
        "adr-021-example.md",
        "adr-022-runtime-policy-lifecycle.md",
    )


@pytest.mark.parametrize("path", ["TASKS.md", "docs/PRD.md"])
def test_inspect_project_facts_rejects_stale_current_source_contract(
    fixture_repository: Path, path: str
) -> None:
    # Given a canonical current document declares an obsolete migration as its source.
    checker = _load_checker()
    surface = fixture_repository / path
    surface.write_text(
        surface.read_text(encoding="utf-8").replace(
            "migration=m71_current", "migration=m59_conversation_artifacts"
        ),
        encoding="utf-8",
    )

    # When source-derived facts are checked, the explicit current contract fails.
    with pytest.raises(RuntimeError, match="current-source contract"):
        checker.inspect_project_facts(fixture_repository)


@pytest.mark.parametrize("status", ["current", "approved"])
def test_inspect_project_facts_rejects_future_program_as_committed_work(
    fixture_repository: Path, status: str
) -> None:
    # Given a separately gated future program is represented as current or approved.
    checker = _load_checker()
    index = fixture_repository / "docs/exec-plans/index.md"
    index.write_text(
        index.read_text(encoding="utf-8").replace(
            "future-program: rubric; status=deferred",
            f"future-program: rubric; status={status}",
        ),
        encoding="utf-8",
    )

    # When the project facts are checked, roadmap scope cannot silently expand.
    with pytest.raises(RuntimeError, match="deferred-program contract"):
        checker.inspect_project_facts(fixture_repository)


def test_inspect_project_facts_rejects_runtime_policy_adr_contract_drift(
    fixture_repository: Path,
) -> None:
    # Given ADR-022 no longer identifies the source-derived migration contract.
    checker = _load_checker()
    adr = fixture_repository / "docs/design-docs/adr-022-runtime-policy-lifecycle.md"
    adr.write_text(
        adr.read_text(encoding="utf-8").replace("migration=m71_current", "migration=m70_current"),
        encoding="utf-8",
    )

    # When checked, the accepted runtime-policy decision fails closed.
    with pytest.raises(RuntimeError, match="ADR-022 runtime-policy contract"):
        checker.inspect_project_facts(fixture_repository)


def test_inspect_project_facts_rejects_false_profile_e2e_coverage(
    fixture_repository: Path,
) -> None:
    # Given the E2E matrix claims the still-uncovered profile flow is tested.
    checker = _load_checker()
    coverage = fixture_repository / "docs/e2e-coverage.md"
    coverage.write_text(
        coverage.read_text(encoding="utf-8").replace(
            "profile-personalization=untested", "profile-personalization=tested"
        ),
        encoding="utf-8",
    )

    # When current facts are checked, the false coverage marker is rejected.
    with pytest.raises(RuntimeError, match="E2E current-source contract"):
        checker.inspect_project_facts(fixture_repository)


def test_inspect_project_facts_allows_explicit_historical_provenance(
    fixture_repository: Path,
) -> None:
    # Given a dated history line names an old migration without presenting it as current.
    checker = _load_checker()
    architecture = fixture_repository / "docs/ARCHITECTURE.md"
    architecture.write_text(
        architecture.read_text(encoding="utf-8")
        + "2026-06-06: artifacts introduced by m59_conversation_artifacts.\n",
        encoding="utf-8",
    )

    # When canonical current facts are checked, accurate historical provenance is preserved.
    facts = checker.inspect_project_facts(fixture_repository)

    assert facts[2] == "m71_current"


def test_cli_returns_zero_for_complete_tracked_metadata(fixture_repository: Path) -> None:
    # Given a complete tracked metadata fixture repository.
    command = [sys.executable, str(SCRIPT_PATH), "--repo-root", str(fixture_repository)]

    # When the public CLI checks it.
    completed = subprocess.run(command, cwd=fixture_repository, capture_output=True, text=True)

    # Then its binary observable is success and it prints the derived head.
    assert completed.returncode == 0, completed.stderr
    assert "m71_current" in completed.stdout


def test_cli_reports_malformed_utf8_without_traceback(fixture_repository: Path) -> None:
    # Given one tracked canonical surface contains malformed UTF-8 bytes.
    (fixture_repository / "README.md").write_bytes(b"\xff\xfe")
    command = [sys.executable, str(SCRIPT_PATH), "--repo-root", str(fixture_repository)]

    # When the public CLI checks it, the failure is bounded and path-safe.
    completed = subprocess.run(command, cwd=fixture_repository, capture_output=True, text=True)

    assert completed.returncode == 1
    assert "tracked metadata is not readable UTF-8: README.md" in completed.stderr
    assert "Traceback" not in completed.stderr
    assert str(fixture_repository) not in completed.stderr
