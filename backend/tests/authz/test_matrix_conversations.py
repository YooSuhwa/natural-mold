"""D-09 matrix: conversation-family PRIVATE rows and their agent re-checks (plan 9.3)."""

import uuid
from datetime import UTC, datetime

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.authz.policies import RES
from app.models.conversation import Conversation
from tests.authz.agent_route_fixture import seed_agent
from tests.authz.d04_support import seed_scope
from tests.authz.matrix_support import (
    PRIVATE_ACTORS,
    Actor,
    Matrix,
    Mode,
    Probe,
    Target,
    cases,
    matrix,
)
from tests.authz.unit_scope_fixture import TEST_ORG_ID, TEST_TENANT_ID
from tests.conftest import TEST_USER_ID, TestSession

__all__ = ["matrix"]  # fixture re-export
RUN = RES("agent", "can_run", param="agent_id", min_visible="can_run")
VIEW_CONFIG = RES("agent", "can_view_config", param="agent_id", min_visible="can_run")


@pytest.fixture(autouse=True)
async def _scope(db: AsyncSession) -> None:
    await seed_scope(db)


async def _conversation() -> tuple[uuid.UUID, uuid.UUID]:
    """My agent and my conversation with it, in the current organization."""
    agent_id = uuid.UUID(await seed_agent(owner=TEST_USER_ID))
    async with TestSession() as db:
        conversation = Conversation(
            agent_id=agent_id,
            title="matrix",
            org_id=TEST_ORG_ID,
            tenant_id=TEST_TENANT_ID,
            user_id=TEST_USER_ID,
            agent_name_snapshot="Agent",
            last_activity_at=datetime.now(UTC),
        )
        db.add(conversation)
        await db.commit()
        return agent_id, conversation.id


PRIVATE_PROBES = (
    Probe("GET /api/conversations/{conversation_id}"),
    Probe("PATCH /api/conversations/{conversation_id}", {"title": "renamed"}),
    Probe("GET /api/conversations/{conversation_id}/traces"),
    Probe("GET /api/conversations/{conversation_id}/artifacts"),
    Probe("DELETE /api/conversations/{conversation_id}", ok=204),
)


@pytest.mark.parametrize(("probe", "actor", "mode"), cases(PRIVATE_PROBES, PRIVATE_ACTORS), ids=str)
async def test_private_rows_answer_alike_in_every_mode(
    client: AsyncClient, matrix: Matrix, probe: Probe, actor: Actor, mode: Mode
) -> None:
    _, conversation_id = await _conversation()
    path = {"conversation_id": str(conversation_id)}
    await matrix.check_private(
        client, probe, actor, mode, Target("conversations", "conversation", conversation_id, path)
    )


# F-06: someone else's agent loads under the caller's ``agent#can_run`` once ``agent`` is
# enforced (``run_agent_loader``); the owner's own agent stays the private fast path, so the
# owner never reaches OpenFGA on these routes (``down`` answers like legacy).
OWNER_FAST_PATH = {("owner", "down"): 200}
AGENT_PROBES = (
    Probe("POST /api/agents/{agent_id}/conversations", {"title": "x"}, 201, gate=RUN),
    Probe("POST /api/agents/{agent_id}/conversations/draft", None, 201, gate=RUN),
    # Listing my conversations under an agent: no re-check for the owner (9.3).
    Probe("GET /api/agents/{agent_id}/conversations", known=OWNER_FAST_PATH, gate=RUN),
)


@pytest.mark.parametrize(("probe", "actor", "mode"), cases(AGENT_PROBES), ids=str)
async def test_agent_scoped_conversation_routes_recheck_can_run(
    client: AsyncClient, matrix: Matrix, probe: Probe, actor: Actor, mode: Mode
) -> None:
    agent_id, _ = await _conversation()
    target = Target("agents", "agent", agent_id, {"agent_id": str(agent_id)}, "consumer")
    await matrix.check(client, probe, actor, mode, target)


# Legacy keeps the conversation owner's access (D-01). A conversation and its agent share
# an organization by construction, so ``foreign`` is covered by the PRIVATE table above.
LEGACY_OWNER = {(actor, "legacy"): 200 for actor in ("granted", "visible", "stranger")}
TRACES = Probe(
    "GET /api/conversations/{conversation_id}/debug/traces", known=LEGACY_OWNER, gate=VIEW_CONFIG
)


@pytest.mark.parametrize(
    ("probe", "actor", "mode"),
    cases((TRACES,), ("owner", "granted", "visible", "stranger")),
    ids=str,
)
async def test_debug_traces_need_the_agents_can_view_config(
    client: AsyncClient, matrix: Matrix, probe: Probe, actor: Actor, mode: Mode
) -> None:
    agent_id, conversation_id = await _conversation()
    path = {"conversation_id": str(conversation_id)}
    await matrix.check(client, probe, actor, mode, Target("agents", "agent", agent_id, path))
