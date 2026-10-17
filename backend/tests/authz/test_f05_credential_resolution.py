"""F-05: run-time credential resolution by mode and run identity (plan 11.1, 11.3).

Done-when: a shared ``per_user`` agent runs on the caller's own connection.
"""

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent_runtime.credential_access import AgentCredentialAccess
from app.agent_runtime.credential_resolution import resolve_llm_api_key_for_agent
from app.authz.runtime import legacy_service
from app.exceptions import AppError
from app.models.organization import OrganizationMember
from app.services import chat_service
from tests.authz.e01_support import THIRD_USER_ID, add_member, seed
from tests.authz.f05_support import (
    CALLER,
    OWNER,
    bind,
    enforce,
    identity,
    loaded,
    per_user_server,
    per_user_tool,
    run_config,
    secret,
    shared_agent,
)
from tests.authz.unit_scope_fixture import TEST_ORG_ID, unit_context

ME = f"user:{CALLER}"


@pytest.fixture(autouse=True)
async def _scope(db: AsyncSession) -> None:
    await seed(db)


def _can_run(agent) -> tuple[str, str, str]:  # noqa: ANN001
    return (ME, "can_run", f"agent:{agent.id}")


async def test_a_shared_per_user_agent_runs_on_the_callers_connection(db: AsyncSession) -> None:
    agent = await shared_agent(db)
    tool = await per_user_tool(db, agent)
    await bind(db, OWNER, "tool", tool.id, await secret(db, OWNER, "owner-token"))
    mine = await secret(db, CALLER, "caller-token")
    await bind(db, CALLER, "tool", tool.id, mine)
    authz = enforce(_can_run(agent), (ME, "can_use", f"tool:{tool.id}"))
    [entry] = await run_config(db, agent, authz)
    assert entry["credentials"]["token"] == "caller-token"
    assert entry["credential_id"] == str(mine.id)
    assert entry["credential_subject_user_id"] == str(CALLER)


async def test_a_shared_per_user_mcp_server_uses_the_callers_connection(db: AsyncSession) -> None:
    agent = await shared_agent(db)
    server = await per_user_server(db, agent)
    await bind(db, CALLER, "mcp_server", server.id, await secret(db, CALLER, "caller-mcp"))
    authz = enforce(_can_run(agent), (ME, "can_use", f"mcp_server:{server.id}"))
    [entry] = await run_config(db, agent, authz)
    assert entry["credentials"]["token"] == "caller-mcp"


async def test_a_missing_connection_is_a_typed_error_with_resource_ids(db: AsyncSession) -> None:
    agent = await shared_agent(db)
    tool = await per_user_tool(db, agent)
    server = await per_user_server(db, agent)
    authz = enforce(
        _can_run(agent),
        (ME, "can_use", f"tool:{tool.id}"),
        (ME, "can_use", f"mcp_server:{server.id}"),
    )
    with pytest.raises(AppError) as caught:
        await run_config(db, agent, authz)
    assert (caught.value.code, caught.value.status) == ("credential_binding_required", 409)
    assert caught.value.details == {
        "resources": [
            {"resource_type": "tool", "resource_id": str(tool.id), "reason": "missing"},
            {"resource_type": "mcp_server", "resource_id": str(server.id), "reason": "missing"},
        ]
    }


async def test_a_connection_whose_credential_lost_can_use_fails_closed(db: AsyncSession) -> None:
    await add_member(db, THIRD_USER_ID)
    agent = await shared_agent(db)
    tool = await per_user_tool(db, agent)
    lent = await secret(db, THIRD_USER_ID, "third-token")  # was shared to the caller
    await bind(db, CALLER, "tool", tool.id, lent)
    still = (ME, "can_use", f"credential:{lent.id}")
    base = (_can_run(agent), (ME, "can_use", f"tool:{tool.id}"))
    [entry] = await run_config(db, agent, enforce(*base, still))
    assert entry["credentials"]["token"] == "third-token"
    with pytest.raises(AppError) as caught:
        await run_config(db, agent, enforce(*base))
    assert caught.value.details["resources"][0]["reason"] == "unusable"  # type: ignore[index]


async def test_a_connection_outliving_resource_access_is_refused(db: AsyncSession) -> None:
    agent = await shared_agent(db)
    tool = await per_user_tool(db, agent)
    await bind(db, CALLER, "tool", tool.id, await secret(db, CALLER, "caller-token"))
    with pytest.raises(AppError) as caught:
        await run_config(db, agent, enforce(_can_run(agent)))
    assert caught.value.status == 404


async def test_a_fixed_agent_uses_the_owners_connection_for_every_caller(db: AsyncSession) -> None:
    agent = await shared_agent(db, mode="fixed")
    tool = await per_user_tool(db, agent)
    await bind(db, OWNER, "tool", tool.id, await secret(db, OWNER, "owner-token"))
    await bind(db, CALLER, "tool", tool.id, await secret(db, CALLER, "caller-token"))
    [entry] = await run_config(db, agent, enforce(_can_run(agent)))
    assert entry["credentials"]["token"] == "owner-token"
    assert entry["credential_subject_user_id"] == str(OWNER)


async def test_a_fixed_run_refuses_the_connection_of_an_owner_who_left(db: AsyncSession) -> None:
    agent = await shared_agent(db, mode="fixed")
    tool = await per_user_tool(db, agent)
    await bind(db, OWNER, "tool", tool.id, await secret(db, OWNER, "owner-token"))
    fetched = await loaded(db, agent)  # loaded before the owner left
    member = await db.get(OrganizationMember, (TEST_ORG_ID, OWNER))
    assert member is not None
    await db.delete(member)
    await db.commit()
    with pytest.raises(AppError) as caught:
        await chat_service.build_tools_config(
            fetched,
            context=unit_context(CALLER),
            db=db,
            identity=identity(agent),
            authz=enforce(_can_run(agent)),
        )
    assert caught.value.code == "credential_binding_required"


async def test_shared_mode_uses_the_resource_owners_credential(db: AsyncSession) -> None:
    agent = await shared_agent(db)
    tool = await per_user_tool(db, agent)
    tool.credential_mode = "shared"
    tool.credential_id = (await secret(db, OWNER, "owner-token")).id
    await db.commit()
    authz = enforce(_can_run(agent), (ME, "can_use", f"tool:{tool.id}"))
    [entry] = await run_config(db, agent, authz)
    assert entry["credentials"]["token"] == "owner-token"


async def test_legacy_keeps_shared_agents_out_and_owner_runs_working(db: AsyncSession) -> None:
    agent = await shared_agent(db)
    tool = await per_user_tool(db, agent)
    await bind(db, OWNER, "tool", tool.id, await secret(db, OWNER, "owner-token"))
    with pytest.raises(AppError) as caught:
        await run_config(db, agent, legacy_service())
    assert caught.value.status == 404
    [entry] = await run_config(db, agent, legacy_service(), caller=OWNER)
    assert entry["credentials"]["token"] == "owner-token"


async def test_read_only_masking_collects_connections_without_deciding(db: AsyncSession) -> None:
    agent = await shared_agent(db)
    tool = await per_user_tool(db, agent)
    await per_user_server(db, agent)  # no connection: skipped, no error
    await bind(db, CALLER, "tool", tool.id, await secret(db, CALLER, "caller-token"))
    entries = await run_config(db, agent, enforce(_can_run(agent)), read_only=True)
    assert [e["credentials"] for e in entries] == [
        {"token": "caller-token", "api_key": "caller-token"},
        None,
    ]


async def test_a_shared_per_user_agent_uses_the_callers_llm_key(db: AsyncSession) -> None:
    agent = await shared_agent(db)
    agent.llm_credential_id = (await secret(db, OWNER, "sk-owner", "openai")).id
    await db.commit()
    await secret(db, CALLER, "sk-caller", "openai")
    fetched = await loaded(db, agent)
    access = AgentCredentialAccess(unit_context(CALLER), identity(agent))
    assert await resolve_llm_api_key_for_agent(db, fetched, access) == "sk-caller"
