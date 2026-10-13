"""Native B08 lifecycle migration and complete-graph acceptance."""

from __future__ import annotations

import json
import os
from pathlib import Path

import psycopg
import pytest
from sqlalchemy import text

from tests.authz.agent_lifecycle_fixture import (
    LifecycleGraphProof,
    RegisteredLifecycleFixture,
    registered_lifecycle_fixture,
)
from tests.authz.agent_lifecycle_graph_seed import assert_lifecycle_graph

pytestmark = pytest.mark.integration


def _write_graph_receipt(
    fixture: RegisteredLifecycleFixture,
    proof: LifecycleGraphProof,
) -> None:
    graph = fixture.graph
    destination = Path(os.environ["MOLDY_PG_TEST_RECEIPT"]).with_name(
        "lifecycle-graph-receipt.json"
    )
    payload = {
        "tenant_id": str(graph.tenant_id),
        "org_id": str(graph.org_id),
        "root_owner_id": str(graph.root_owner_id),
        "child_owner_id": str(graph.child_owner_id),
        "root_agent_id": str(graph.root_agent_id),
        "child_agent_ids": [str(value) for value in graph.child_agent_ids],
        "conversation_ids": [str(value) for value in graph.conversation_ids],
        "run_ids": [str(value) for value in graph.run_ids],
        "input_ids": [str(value) for value in graph.input_ids],
        "row_counts": dict(proof.row_counts),
        "row_ids": {surface: list(ids) for surface, ids in proof.row_ids},
        "attachment_bytes": proof.attachment_bytes,
        "artifact_version_bytes": list(proof.artifact_version_bytes),
        "attachment_sha256": proof.attachment_sha256,
        "artifact_version_sha256": list(proof.artifact_version_sha256),
        "observed_stage_counts": list(fixture.sentinel.observed_stage_counts),
        "provider_invocations": fixture.sentinel.provider_invocations,
    }
    destination.write_text(json.dumps(payload, sort_keys=True) + "\n")


def test_agent_lifecycle_lane_runs_deployable_b11_head() -> None:
    # Given the disposable lane already upgraded through the deployable chain.
    dsn = os.environ["DATABASE_URL_SYNC"]

    # When the native Alembic version is read from that owned lane.
    with psycopg.connect(dsn) as connection:
        row = connection.execute("SELECT version_num FROM alembic_version").fetchone()

    # Then the lane reaches B11 while retaining B08 lifecycle history, not ORM create_all.
    assert row == ("b11_company_administration",)


async def test_full_lifecycle_graph_has_real_auth_roles_rows_and_bytes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given actors registered through real JWT cookies and CSRF mutations.
    async with registered_lifecycle_fixture(monkeypatch) as fixture:
        actors = fixture.actors
        graph = fixture.graph

        # When the full graph is independently read before any lifecycle action.
        proof = await assert_lifecycle_graph(fixture.sessions, graph)
        async with fixture.sessions() as db:
            direct_admin = await db.scalar(
                text(
                    "SELECT count(*) FROM organization_role_grants "
                    "WHERE principal_type='user' AND principal_id=:actor AND role='admin'"
                ),
                {"actor": actors.direct_org_admin_id},
            )
            group_admin = await db.scalar(
                text(
                    "SELECT count(*) FROM organization_role_grants grant_row "
                    "JOIN group_members member ON member.group_id=grant_row.principal_id "
                    "WHERE grant_row.principal_type='group' AND member.user_id=:actor "
                    "AND grant_row.role='admin'"
                ),
                {"actor": actors.group_org_admin_id},
            )
            company_admin = await db.scalar(
                text(
                    "SELECT count(*) FROM tenant_role_grants WHERE user_id=:actor AND role='admin'"
                ),
                {"actor": actors.company_admin_id},
            )
            operator = await db.scalar(
                text("SELECT is_super_user FROM users WHERE id=:actor"),
                {"actor": actors.operator_id},
            )
            revoked = await db.scalar(
                text(
                    "SELECT count(*) FROM organization_members "
                    "WHERE user_id=:actor AND status='removed'"
                ),
                {"actor": actors.revoked_admin_id},
            )
            inactive = await db.scalar(
                text("SELECT is_active FROM users WHERE id=:actor"),
                {"actor": actors.inactive_admin_id},
            )
        fixture.sentinel.observe("cancel-signaled")
        fixture.sentinel.observe("terminal-ACK")

        # Then every authority and retained descendant is concrete and unequal.
        assert graph.root_owner_id != graph.child_owner_id
        assert len({graph.root_agent_id, *graph.child_agent_ids}) == 3
        assert (direct_admin, group_admin, company_admin) == (1, 1, 1)
        assert operator is True
        assert revoked == 1
        assert inactive is False
        assert dict(proof.row_counts) == {
            "agents": 3,
            "conversations": 3,
            "conversation_runs": 3,
            "conversation_run_inputs": 3,
            "message_attachments": 1,
            "conversation_artifacts": 1,
            "artifact_versions": 2,
            "agent_api_runs": 1,
            "agent_trigger_runs": 1,
            "share_links": 1,
            "mcp_app_invocation_bindings": 1,
            "conversation_run_agents": 9,
            "checkpoints": 2,
            "agent_subagents": 2,
            "message_events": 1,
            "message_event_chunks": 1,
            "message_feedbacks": 1,
            "conversation_pinned_summaries": 1,
            "conversation_run_metrics": 3,
            "token_usages": 1,
            "agent_deployments": 1,
            "agent_api_threads": 1,
            "agent_triggers": 1,
            "mcp_servers": 1,
            "mcp_tools": 1,
        }
        assert proof.attachment_bytes > 0
        assert proof.artifact_version_bytes[0] != proof.artifact_version_bytes[1]
        assert proof.artifact_version_sha256[0] != proof.artifact_version_sha256[1]
        assert fixture.sentinel.observed_stage_counts == (1, 8, 1, 1, 1)
        assert fixture.sentinel.provider_invocations == 0
        _write_graph_receipt(fixture, proof)
