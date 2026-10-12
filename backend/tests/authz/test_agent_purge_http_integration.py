from __future__ import annotations

from uuid import uuid4

import pytest
from sqlalchemy import func, select, update

from app.models.agent_purge import AgentPurgeReceipt
from app.models.conversation_history import ConversationHistoryManifest
from app.models.organization import OrganizationMember, TenantMember
from tests.authz.agent_lifecycle_fixture import registered_lifecycle_fixture
from tests.authz.agent_purge_native_support import (
    acknowledge_archived_runs,
    actor_client,
    native_checkpointer,
    seed_missing_purge_checkpoints,
)

pytestmark = pytest.mark.integration


async def test_real_jwt_csrf_role_and_name_denials_create_no_purge_state(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: the complete graph is archived and every durable owner has ACKed.
    async with registered_lifecycle_fixture(monkeypatch) as fixture:
        graph = fixture.graph
        async with actor_client(fixture, fixture.actors.direct_org_admin_id) as (client, csrf):
            archived = await client.delete(
                f"/api/agents/{graph.root_agent_id}", headers={"X-CSRF-Token": csrf}
            )
            assert archived.status_code == 204, archived.text
        await acknowledge_archived_runs(fixture)
        async with fixture.sessions() as db:
            await db.execute(
                update(OrganizationMember)
                .where(
                    OrganizationMember.org_id == fixture.actors.org_id,
                    OrganizationMember.user_id == fixture.actors.owner_two_id,
                )
                .values(status="removed")
            )
            await db.execute(
                update(TenantMember)
                .where(
                    TenantMember.tenant_id == fixture.actors.tenant_id,
                    TenantMember.user_id == fixture.actors.owner_two_id,
                )
                .values(status="removed")
            )
            await db.commit()

        # When: wrong roles and then an authorized wrong name use real auth/CSRF.
        expected = (
            (fixture.actors.owner_one_id, 403),
            (fixture.actors.owner_two_id, 404),
            (fixture.actors.company_admin_id, 403),
            (fixture.actors.operator_id, 403),
            (fixture.actors.revoked_admin_id, 404),
        )
        for actor_id, status in expected:
            async with actor_client(fixture, actor_id) as (client, csrf):
                denied = await client.post(
                    f"/api/agents/{graph.root_agent_id}/purge",
                    json={"confirmation_name": "Lifecycle root"},
                    headers={"X-CSRF-Token": csrf},
                )
                assert denied.status_code == status, denied.text
        async with actor_client(fixture, fixture.actors.direct_org_admin_id) as (client, csrf):
            mismatch = await client.post(
                f"/api/agents/{graph.root_agent_id}/purge",
                json={"confirmation_name": "Lifecycle root "},
                headers={"X-CSRF-Token": csrf},
            )
            assert mismatch.status_code == 409, mismatch.text
            assert mismatch.json()["error"]["code"] == "AGENT_PURGE_NAME_MISMATCH"
            missing = await client.post(
                f"/api/agents/{uuid4()}/purge",
                json={"confirmation_name": "missing"},
                headers={"X-CSRF-Token": csrf},
            )
            assert missing.status_code == 404, missing.text

        # Then: denial occurred before receipt or snapshot writes.
        async with fixture.sessions() as db:
            assert (
                await db.scalar(
                    select(func.count())
                    .select_from(AgentPurgeReceipt)
                    .where(AgentPurgeReceipt.agent_id_snapshot == graph.root_agent_id)
                )
                == 0
            )
            assert (
                await db.scalar(
                    select(func.count())
                    .select_from(ConversationHistoryManifest)
                    .where(ConversationHistoryManifest.conversation_id.in_(graph.conversation_ids))
                )
                == 0
            )


async def test_real_jwt_csrf_exact_purge_and_replay_share_one_receipt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: real PostgreSQL checkpoints and a fully ACKed archived graph.
    async with registered_lifecycle_fixture(monkeypatch) as fixture:
        graph = fixture.graph
        async with native_checkpointer():
            await seed_missing_purge_checkpoints(fixture)
            async with actor_client(fixture, fixture.actors.direct_org_admin_id) as (client, csrf):
                archived = await client.delete(
                    f"/api/agents/{graph.root_agent_id}", headers={"X-CSRF-Token": csrf}
                )
                assert archived.status_code == 204, archived.text
            await acknowledge_archived_runs(fixture)

            # When: the admin purges and replays the same exact request.
            async with actor_client(fixture, fixture.actors.direct_org_admin_id) as (client, csrf):
                first = await client.post(
                    f"/api/agents/{graph.root_agent_id}/purge",
                    json={"confirmation_name": "Lifecycle root"},
                    headers={"X-CSRF-Token": csrf},
                )
                wrong_name = await client.post(
                    f"/api/agents/{graph.root_agent_id}/purge",
                    json={"confirmation_name": "Lifecycle root "},
                    headers={"X-CSRF-Token": csrf},
                )
            replay_denials = (
                (fixture.actors.owner_one_id, 403),
                (fixture.actors.owner_two_id, 403),
                (fixture.actors.revoked_admin_id, 404),
            )
            for actor_id, expected_status in replay_denials:
                async with actor_client(fixture, actor_id) as (client, csrf):
                    denied = await client.post(
                        f"/api/agents/{graph.root_agent_id}/purge",
                        json={"confirmation_name": "Lifecycle root"},
                        headers={"X-CSRF-Token": csrf},
                    )
                    assert denied.status_code == expected_status, denied.text
            async with actor_client(fixture, fixture.actors.direct_org_admin_id) as (client, csrf):
                replay = await client.post(
                    f"/api/agents/{graph.root_agent_id}/purge",
                    json={"confirmation_name": "Lifecycle root"},
                    headers={"X-CSRF-Token": csrf},
                )

            # Then: HTTP reports one completed durable receipt.
            assert first.status_code == 200, first.text
            assert wrong_name.status_code == 409, wrong_name.text
            assert wrong_name.json()["error"]["code"] == "AGENT_PURGE_NAME_MISMATCH"
            assert replay.status_code == 200, replay.text
            assert first.json() == replay.json()
            async with fixture.sessions() as db:
                assert (
                    await db.scalar(
                        select(func.count())
                        .select_from(AgentPurgeReceipt)
                        .where(AgentPurgeReceipt.agent_id_snapshot == graph.root_agent_id)
                    )
                    == 1
                )
