"""Static contract for the Task 10 native PostgreSQL sources (internal I23).

Ordinary and AST-only: never imports or collects a native module. The five
native modules must exist, be module-level ``integration``-marked, load the
canonical-lane fixture (whose cleanup assertions every native test inherits),
define every required surface test, and never be imported by an ordinary
module — directly, via ``from`` imports, or through a ``pytest_plugins`` string.
"""

from __future__ import annotations

import ast
from collections.abc import Mapping
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[2]
AUTHZ = "tests/authz"
FIXTURE = "tests.authz.task10_native_fixture"
INCOMPLETE = "native_source_contract_incomplete"
LEAKED = "native_source_imported_by_ordinary"

SURFACES: dict[str, frozenset[str]] = {
    "test_task10_chat_integration": frozenset(
        {
            "test_native_chat_lease_first_archive_commits_during_parked_leaf_then_finishes",
            "test_native_chat_archive_first_terminalizes_without_dispatch",
            "test_native_chat_materialization_transaction_gaps",
            "test_native_chat_checkpoint_lineage_is_exact",
            "test_native_chat_four_tool_classes_dispatch_only_through_archived_lease",
            "test_native_chat_mcp_timeout_cleans_up_before_marker",
        }
    ),
    "test_task10_external_integration": frozenset(
        {
            "test_native_agent_api_archive_commits_during_parked_send_then_succeeds",
            "test_native_agent_api_archive_first_fails_envelope_exactly_once",
            "test_native_trigger_claim_first_finishes_through_archived_lease",
            "test_native_trigger_archive_first_creates_nothing_and_restore_never_replays",
        }
    ),
    "test_task10_queue_integration": frozenset(
        {
            "test_native_claim_first_transaction_blocks_archive_then_fails_claimed_run",
            "test_native_archive_first_blocks_claim_and_cancels_pending_input",
            "test_native_restart_terminalizes_lost_lease_without_reconstruction",
            "test_native_restore_never_replays_canceled_input",
        }
    ),
    "test_task10_authority_integration": frozenset(
        {
            "test_native_revocation_commits_during_parked_leaf_and_wins_next_dispatch",
            "test_native_participant_expansion_after_archive_never_launches",
            "test_native_tenant_suspension_freezes_terminal_fields_and_entry_rows",
        }
    ),
    "test_task10_lifecycle_compat_integration": frozenset(
        {
            "test_native_org_archive_versus_b08_agent_archive_without_deadlock",
            "test_native_creation_versus_archive_has_one_bounded_winner",
            "test_native_invite_accept_versus_archive_both_orders",
            "test_native_restore_never_replays_or_reenables",
        }
    ),
}
NATIVE = frozenset(f"tests.authz.{name}" for name in SURFACES) | {FIXTURE}


def _is_integration_mark(node: ast.expr) -> bool:
    return isinstance(node, ast.Attribute) and node.attr == "integration"


def _module_issues(name: str, source: str | None) -> list[str]:
    if source is None:
        return [f"{INCOMPLETE}:{name}:missing"]
    tree = ast.parse(source)
    issues: list[str] = []
    marked = plugins = False
    tests: set[str] = set()
    for node in tree.body:
        if isinstance(node, ast.Assign) and isinstance(node.targets[0], ast.Name):
            target, value = node.targets[0].id, node.value
            if target == "pytestmark":
                values = value.elts if isinstance(value, ast.List | ast.Tuple) else [value]
                marked = any(_is_integration_mark(item) for item in values)
            if target == "pytest_plugins":
                plugins = FIXTURE in {getattr(c, "value", None) for c in ast.walk(value)}
        if isinstance(node, ast.AsyncFunctionDef | ast.FunctionDef):
            tests.add(node.name)
    if not marked:
        issues.append(f"{INCOMPLETE}:{name}:not_integration_marked")
    if not plugins:
        issues.append(f"{INCOMPLETE}:{name}:canonical_fixture_missing")
    issues += [f"{INCOMPLETE}:{name}:{test}" for test in sorted(SURFACES[name] - tests)]
    return issues


def _imported(source: str) -> set[str]:
    names: set[str] = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module)
            names.update(f"{node.module}.{alias.name}" for alias in node.names)
        elif isinstance(node, ast.Assign) and any(
            isinstance(target, ast.Name) and target.id == "pytest_plugins"
            for target in node.targets
        ):
            names.update(
                c.value
                for c in ast.walk(node.value)
                if isinstance(c, ast.Constant) and isinstance(c.value, str)
            )
    return names


def check_native_sources(
    natives: Mapping[str, str | None], ordinary: Mapping[str, str]
) -> list[str]:
    issues = [issue for name in SURFACES for issue in _module_issues(name, natives.get(name))]
    for path, source in sorted(ordinary.items()):
        issues += [f"{LEAKED}:{path}:{hit}" for hit in sorted(_imported(source) & NATIVE)]
    return issues


def _dotted(path: Path) -> str:
    return ".".join(path.relative_to(BACKEND).with_suffix("").parts)


def _candidate() -> tuple[dict[str, str | None], dict[str, str]]:
    natives: dict[str, str | None] = {}
    for name in SURFACES:
        path = BACKEND / AUTHZ / f"{name}.py"
        natives[name] = path.read_text() if path.is_file() else None
    ordinary = {
        str(path.relative_to(BACKEND)): path.read_text()
        for root in ("app", "tests", "scripts")
        for path in (BACKEND / root).rglob("*.py")
        if _dotted(path) not in NATIVE and "__pycache__" not in path.parts
    }
    return natives, ordinary


def test_native_sources_are_isolated_and_complete() -> None:
    natives, ordinary = _candidate()
    assert check_native_sources(natives, ordinary) == []

    broken = dict(natives)
    chat = natives["test_task10_chat_integration"]
    assert chat is not None
    broken["test_task10_chat_integration"] = chat.replace(
        "def test_native_chat_checkpoint_lineage_is_exact", "def renamed_lineage"
    )
    broken["test_task10_queue_integration"] = None
    leak = {"tests/x.py": f"import {FIXTURE}\n", "tests/y.py": f'pytest_plugins = ["{FIXTURE}"]'}
    issues = check_native_sources(broken, leak)
    assert all(issue.startswith((INCOMPLETE, LEAKED)) for issue in issues)
    assert {issue.split(":", 1)[0] for issue in issues} == {INCOMPLETE, LEAKED}
    assert f"{INCOMPLETE}:test_task10_queue_integration:missing" in issues
    assert (
        f"{INCOMPLETE}:test_task10_chat_integration:test_native_chat_checkpoint_lineage_is_exact"
        in issues
    )
    assert {f"{LEAKED}:tests/x.py:{FIXTURE}", f"{LEAKED}:tests/y.py:{FIXTURE}"} <= set(issues)


def test_unmarked_or_fixtureless_native_module_is_incomplete() -> None:
    natives, _ordinary = _candidate()
    source = natives["test_task10_authority_integration"]
    assert source is not None
    stripped = source.replace("pytestmark = pytest.mark.integration", "pytestmark = []")
    stripped = stripped.replace(f'pytest_plugins = ["{FIXTURE}"]', "pytest_plugins = []")
    issues = check_native_sources({**natives, "test_task10_authority_integration": stripped}, {})
    assert issues == [
        f"{INCOMPLETE}:test_task10_authority_integration:not_integration_marked",
        f"{INCOMPLETE}:test_task10_authority_integration:canonical_fixture_missing",
    ]


def test_native_fixture_requires_the_canonical_lane() -> None:
    fixture = (BACKEND / AUTHZ / "task10_native_fixture.py").read_text()
    assert "_runner_dsns()" in fixture and "prepared_run_lease_enabled" in fixture
    assert "_assert_runtime_clean" in fixture
