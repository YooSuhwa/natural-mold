"""F-05 review follow-ups: shared-agent masking scope, URL-less servers, inactive rows."""

from datetime import UTC, datetime

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.authz import run_agent_scope
from app.credentials import run_resolution
from app.exceptions import AppError
from app.models.conversation import Conversation
from app.models.tool import AgentToolLink
from app.services.chat.secrets import collect_conversation_secret_values
from tests.authz.d04_support import add_tool
from tests.authz.e01_support import seed
from tests.authz.f05_support import (
    CALLER,
    OWNER,
    bind,
    enforce,
    per_user_server,
    per_user_tool,
    run_config,
    secret,
    shared_agent,
)
from tests.authz.unit_scope_fixture import TEST_ORG_ID, TEST_TENANT_ID, unit_context

ME = f"user:{CALLER}"


@pytest.fixture(autouse=True)
async def _scope(db: AsyncSession) -> None:
    await seed(db)


async def _masking_fixture(db: AsyncSession) -> Conversation:
    agent = await shared_agent(db)
    tool = await per_user_tool(db, agent)
    await bind(db, CALLER, "tool", tool.id, await secret(db, CALLER, "caller-conn"))
    shared = await add_tool(db, owner=OWNER, name="shared")
    shared.credential_id = (await secret(db, OWNER, "owner-shared")).id
    shared.credential_mode = "shared"
    db.add(AgentToolLink(agent_id=agent.id, tool_id=shared.id))
    await secret(db, CALLER, "sk-caller-llm", "openai")
    conversation = Conversation(
        agent_id=agent.id,
        user_id=CALLER,
        org_id=TEST_ORG_ID,
        tenant_id=TEST_TENANT_ID,
        title="t",
        agent_name_snapshot=agent.name,
        last_activity_at=datetime.now(UTC),
    )
    db.add(conversation)
    await db.commit()
    return conversation


async def test_a_runnable_shared_agent_conversation_masks_its_secrets(
    db: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    conversation = await _masking_fixture(db)
    service = enforce((ME, "can_run", f"agent:{conversation.agent_id}"))
    monkeypatch.setattr(run_agent_scope, "runtime_authz", lambda: service)
    monkeypatch.setattr(run_resolution, "runtime_authz", lambda: service)
    found = await collect_conversation_secret_values(db, conversation, unit_context(CALLER))
    assert {"caller-conn", "owner-shared", "sk-caller-llm"} <= found


async def test_a_shared_agent_the_caller_cannot_run_is_heuristics_only(db: AsyncSession) -> None:
    conversation = await _masking_fixture(db)  # legacy: shared agents are not runnable
    found = await collect_conversation_secret_values(db, conversation, unit_context(CALLER))
    assert not found & {"caller-conn", "owner-shared", "sk-caller-llm"}


async def test_a_server_without_url_needs_no_connection(db: AsyncSession) -> None:
    agent = await shared_agent(db)
    server = await per_user_server(db, agent)
    server.url = None
    await db.commit()
    authz = enforce(
        (ME, "can_run", f"agent:{agent.id}"), (ME, "can_use", f"mcp_server:{server.id}")
    )
    assert await run_config(db, agent, authz) == []


async def test_an_inactive_connection_is_unusable(db: AsyncSession) -> None:
    agent = await shared_agent(db)
    tool = await per_user_tool(db, agent)
    mine = await secret(db, CALLER, "caller-conn")
    mine.status = "disabled"
    await bind(db, CALLER, "tool", tool.id, mine)
    authz = enforce((ME, "can_run", f"agent:{agent.id}"), (ME, "can_use", f"tool:{tool.id}"))
    with pytest.raises(AppError) as caught:
        await run_config(db, agent, authz)
    assert caught.value.details["resources"][0]["reason"] == "unusable"  # type: ignore[index]


async def test_an_inactive_shared_credential_is_refused(db: AsyncSession) -> None:
    agent = await shared_agent(db)
    tool = await per_user_tool(db, agent)
    credential = await secret(db, OWNER, "owner-shared")
    tool.credential_mode, tool.credential_id = "shared", credential.id
    await db.commit()
    authz = enforce((ME, "can_run", f"agent:{agent.id}"), (ME, "can_use", f"tool:{tool.id}"))
    credential.status = "auth_needed"  # unchanged flow: still resolves (refresh as before)
    await db.commit()
    [entry] = await run_config(db, agent, authz)
    assert entry["credentials"]["token"] == "owner-shared"
    credential.status = "disabled"
    await db.commit()
    with pytest.raises(AppError) as caught:
        await run_config(db, agent, authz)
    assert caught.value.code == "CREDENTIAL_SUBJECT_MISMATCH"


async def test_an_auth_needed_connection_still_resolves(db: AsyncSession) -> None:
    agent = await shared_agent(db)
    tool = await per_user_tool(db, agent)
    mine = await secret(db, CALLER, "caller-conn")
    mine.status = "auth_needed"
    await bind(db, CALLER, "tool", tool.id, mine)
    authz = enforce((ME, "can_run", f"agent:{agent.id}"), (ME, "can_use", f"tool:{tool.id}"))
    [entry] = await run_config(db, agent, authz)
    assert entry["credentials"]["token"] == "caller-conn"
