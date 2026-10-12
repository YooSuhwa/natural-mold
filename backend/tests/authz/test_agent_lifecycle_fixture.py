"""Contracts for the reusable B08 lifecycle graph fixture."""

from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path
from uuid import uuid4

import pytest

BACKEND_ROOT = Path(__file__).resolve().parents[2]
SCRIPTS_ROOT = BACKEND_ROOT.parent / "scripts"
sys.path.insert(0, str(SCRIPTS_ROOT))

from postgres_runner_contract import (  # noqa: E402
    DsnContractError,
    LaneDsns,
    build_lane_dsns,
    parse_lane_dsns,
)
from postgres_runner_runtime import run_test_child  # noqa: E402

from tests.authz.agent_lifecycle_fixture import (  # noqa: E402
    LifecycleActors,
    LifecycleGraph,
    LifecycleSentinel,
)

pytestmark = pytest.mark.integration


def test_agent_lifecycle_fixture_module_exists() -> None:
    # Given the approved B08 fixture module path.
    module_name = "tests.authz.agent_lifecycle_fixture"

    # When Python resolves the reusable fixture.
    specification = importlib.util.find_spec(module_name)

    # Then downstream lifecycle tasks can import one stable public API.
    assert specification is not None


def test_agent_lifecycle_runner_selects_only_named_lifecycle_nodes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given a disposable-lane environment and command capture.
    dsns = build_lane_dsns(
        user="runner",
        password="dummy",
        port=49152,
        database="moldy_pg_lane_lifecycle",
    )
    environment = {
        "DATABASE_URL": dsns.async_url,
        "DATABASE_URL_SYNC": dsns.sync_url,
        "INTEGRATION_DATABASE_URL": dsns.integration_url,
    }
    commands: list[list[str]] = []

    def record_command(
        argv: list[str], **_kwargs: str | int | dict[str, str]
    ) -> subprocess.CompletedProcess[str]:
        commands.append(argv)
        return subprocess.CompletedProcess(argv, 0, "", "")

    monkeypatch.setattr("postgres_runner_runtime.run_command", record_command)

    # When the named lifecycle lane resolves its child command.
    exit_code, _output = run_test_child(environment, "agent-lifecycle")

    # Then no unrelated integration node can enter the lane.
    assert exit_code == 0
    assert commands == [
        [
            str(BACKEND_ROOT / ".venv/bin/pytest"),
            "-q",
            "-p",
            "tests.postgres_execution_plugin",
            "tests/authz/test_agent_lifecycle_fixture.py",
            "tests/integration/test_agent_lifecycle_locking.py",
            "tests/integration/test_agent_lifecycle_migration.py",
            "tests/authz/test_agent_lifecycle_migration_integration.py",
            "tests/authz/test_agent_lifecycle_retention_constraints_integration.py",
            "tests/integration/test_agent_lifecycle_run_ownership.py",
            "tests/integration/test_agent_lifecycle_archive.py",
            "tests/integration/test_agent_deletion_lifecycle.py",
            "tests/integration/test_conversation_history_snapshot.py",
            "tests/integration/test_agent_lifecycle_secondary_fences.py",
            "tests/integration/test_agent_lifecycle_chat_races.py",
            "tests/integration/test_agent_lifecycle_api_races.py",
            "tests/integration/test_agent_lifecycle_trigger_races.py",
            "tests/authz/test_agentless_history_integration.py",
            "tests/authz/test_agent_purge_http_integration.py",
            "tests/integration/test_agent_purge_retention.py",
            "tests/integration/test_agent_purge_races.py",
            "tests/integration/test_agent_lifecycle_end_to_end.py",
            "-m",
            "integration",
        ]
    ]


@pytest.mark.parametrize(
    ("dsns", "reason"),
    [
        (
            LaneDsns(
                "postgresql+asyncpg://runner:dummy@127.0.0.1:49152/moldy",
                "postgresql://runner:dummy@127.0.0.1:49152/moldy",
                "postgresql+psycopg://runner:dummy@127.0.0.1:49152/moldy",
            ),
            "unsafe_database",
        ),
        (
            LaneDsns(
                "postgresql+asyncpg://runner:dummy@127.0.0.1:49152/moldy_pg_lane_one",
                "postgresql://runner:dummy@127.0.0.1:49152/moldy_pg_lane_two",
                "postgresql+psycopg://runner:dummy@127.0.0.1:49152/moldy_pg_lane_one",
            ),
            "target_mismatch",
        ),
    ],
)
def test_lifecycle_dsn_contract_rejects_unsafe_or_mismatched_targets_before_connection(
    dsns: LaneDsns,
    reason: str,
) -> None:
    # Given a shared database name or unequal async/sync target.
    # When the pure trust-boundary parser runs before any connection code.
    with pytest.raises(DsnContractError, match=f"^{reason}$"):
        parse_lane_dsns(dsns)


def test_lifecycle_fixture_exposes_locking_identity_and_graph_ids() -> None:
    # Given distinct actor, scope, Agent, Conversation, Run and Input identities.
    ids = [uuid4() for _ in range(25)]
    actors = LifecycleActors(*ids[:10])
    graph = LifecycleGraph(
        tenant_id=actors.tenant_id,
        org_id=actors.org_id,
        root_owner_id=actors.owner_one_id,
        child_owner_id=actors.owner_two_id,
        root_agent_id=ids[10],
        child_agent_ids=(ids[11], ids[12]),
        conversation_ids=(ids[13], ids[14], ids[15]),
        run_ids=(ids[16], ids[17], ids[18]),
        input_ids=(ids[19],),
        attachment_id=ids[20],
        artifact_id=ids[21],
        artifact_version_ids=(ids[22], ids[23]),
        api_run_id=ids[17],
        trigger_run_id=ids[18],
        share_link_id=ids[24],
        mcp_binding_id=ids[20],
        checkpoint_ids=("checkpoint-root", "checkpoint-branch"),
        attachment_path=Path("attachment.bin"),
        artifact_version_paths=(Path("artifact-v1.bin"), Path("artifact-v2.bin")),
    )

    # When downstream lock tests consume the public frozen fixture values.
    selected = (
        graph.root_agent_id,
        graph.child_agent_ids,
        graph.root_owner_id,
        graph.child_owner_id,
        graph.tenant_id,
        graph.org_id,
        graph.conversation_ids,
        graph.run_ids,
        graph.input_ids,
    )

    # Then no parent/child or owner/scope identity is implicit.
    assert len(set(ids[:20])) == 20
    assert selected[0] not in selected[1]
    assert selected[2] != selected[3]
    assert len(selected[6]) == 3
    assert len(selected[7]) == 3
    assert len(selected[8]) == 1


def test_lifecycle_sentinel_records_observed_phases_without_provider_inference() -> None:
    # Given a sentinel before any runtime acceptance.
    sentinel = LifecycleSentinel()

    # When each explicit owner/task observation is recorded once.
    for stage in (
        "prepared",
        "registered",
        "started",
        "cancel-signaled",
        "terminal-ACK",
    ):
        sentinel.observe(stage)

    # Then ACK is distinct from cancellation and no provider was invoked.
    assert sentinel.observed_stage_counts == (1, 1, 1, 1, 1)
    assert sentinel.cancel_signaled == sentinel.terminal_ack == 1
    assert sentinel.provider_invocations == 0
