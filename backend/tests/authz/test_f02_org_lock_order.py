"""F-02 lock order (E-02, Task 10): no ``FOR SHARE`` on the org row before its exclusive lock.

A link-writing save takes the organization row ``FOR UPDATE`` in the canonical execution
prefix itself (writer intent), so the later delegation lock / revocation epoch bump
reuses it instead of upgrading a share lock — two concurrent saves of one organization
would otherwise deadlock. SQLite drops lock clauses, so the ORM statements are recorded.
"""

from collections.abc import Iterator
from contextlib import contextmanager

import pytest
from httpx import AsyncClient
from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import ORMExecuteState, Session

from app.agent_runtime.assistant.tools.write_tools import build_write_tools
from app.models.agent_subagent import AgentSubAgentLink
from tests.authz.d04_support import add_tool
from tests.authz.e01_support import add_agent, seed
from tests.authz.unit_scope_fixture import unit_context
from tests.conftest import TEST_USER_ID, TestSession

FACTORY = "app.agent_runtime.assistant.tools.write_tools.async_session_factory"


@pytest.fixture(autouse=True)
async def _scope(db: AsyncSession) -> None:
    await seed(db)


@contextmanager
def org_row_locks() -> Iterator[list[str]]:
    """``share`` / ``exclusive`` (FOR UPDATE select or UPDATE) per org-row statement."""
    seen: list[str] = []

    def record(state: ORMExecuteState) -> None:
        statement = state.statement
        tables = {getattr(t, "name", None) for t in getattr(statement, "get_final_froms", list)()}
        if state.is_update and getattr(statement, "table", None) is not None:
            if statement.table.name == "organizations":  # type: ignore[attr-defined]
                seen.append("exclusive")
            return
        lock = getattr(statement, "_for_update_arg", None)
        if state.is_select and lock is not None and "organizations" in tables:
            seen.append("share" if lock.read else "exclusive")

    event.listen(Session, "do_orm_execute", record)
    try:
        yield seen
    finally:
        event.remove(Session, "do_orm_execute", record)


def assert_no_upgrade(seen: list[str]) -> None:
    assert "exclusive" in seen, seen
    assert "share" not in seen[: seen.index("exclusive")], seen


async def test_agent_create_and_update_take_the_org_row_exclusively_first(
    client: AsyncClient, db: AsyncSession
) -> None:
    seed_agent, first, second = (
        await add_agent(db),
        await add_tool(db),
        await add_tool(db, name="b"),
    )
    body = {"name": "n", "system_prompt": "p", "model_id": str(seed_agent.model_id)}
    with org_row_locks() as created:
        response = await client.post("/api/agents", json={**body, "tool_ids": [str(first.id)]})
    assert response.status_code == 201, response.text
    assert_no_upgrade(created)
    path = f"/api/agents/{response.json()['id']}"
    with org_row_locks() as updated:  # revokes ``first`` (epoch bump) and grants ``second``
        response = await client.put(path, json={"tool_ids": [str(second.id)]})
    assert response.status_code == 200, response.text
    assert_no_upgrade(updated)


async def test_assistant_link_writes_and_archive_take_it_exclusively_first(
    client: AsyncClient, db: AsyncSession
) -> None:
    from unittest.mock import patch

    agent, child = await add_agent(db), await add_agent(db)
    await add_tool(db, name="t")
    with patch(FACTORY, TestSession):
        built = build_write_tools(None, agent.id, unit_context(TEST_USER_ID))  # type: ignore[arg-type]
        tools = {t.name: t for t in built}
        with org_row_locks() as added:
            await tools["add_tool_to_agent"].ainvoke({"tool_names": ["t"]})
        with org_row_locks() as removed:
            await tools["remove_tool_from_agent"].ainvoke({"tool_names": ["t"]})
    assert_no_upgrade(added)
    assert_no_upgrade(removed)
    path = f"/api/agents/{agent.id}"
    assert (await client.put(path, json={"sub_agent_ids": [str(child.id)]})).status_code == 200
    with org_row_locks() as archived:  # revokes the parent's subagent delegation
        assert (await client.delete(f"/api/agents/{child.id}")).status_code == 204
    assert_no_upgrade(archived)
    async with TestSession() as check:
        assert await check.get(AgentSubAgentLink, (agent.id, child.id)) is None
