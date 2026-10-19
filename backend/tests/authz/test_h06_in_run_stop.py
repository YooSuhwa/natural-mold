"""H-06: a run that reaches 100% mid-way stops after the current step (plan 8.7 "검사 시점")."""

from types import SimpleNamespace
from typing import Any
from uuid import UUID

import pytest
from langchain.agents import create_agent
from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langchain_core.tools import tool
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent_runtime.runtime import usage_guard
from app.agent_runtime.runtime.usage_guard import (
    STOP_MESSAGE,
    UsageLimitGuardMiddleware,
    with_usage_guard,
)
from app.tenancy.limits import Subject, subject_of
from tests.authz.e01_support import add_agent
from tests.authz.h03_support import TEST_ORG_ID, TEST_TENANT_ID, TEST_USER_ID, seed_world
from tests.authz.h06_support import attribution, fresh_limits, set_agent_limit, spend
from tests.authz.unit_scope_fixture import unit_context
from tests.conftest import TestSession

__all__ = ["fresh_limits"]
pytestmark = [pytest.mark.asyncio, pytest.mark.usefixtures("fresh_limits")]


class FakeToolBindingChatModel(FakeMessagesListChatModel):
    calls: int = 0

    def bind_tools(self, tools: Any, **kwargs: Any) -> "FakeToolBindingChatModel":
        return self

    async def _agenerate(self, *args: Any, **kwargs: Any) -> Any:
        self.calls += 1
        return await super()._agenerate(*args, **kwargs)


@pytest.fixture
async def agent_id(db: AsyncSession, monkeypatch: pytest.MonkeyPatch) -> UUID:
    monkeypatch.setattr(usage_guard, "async_session", TestSession)
    await seed_world(db)
    return (await add_agent(db)).id


def _cfg(agent_id: UUID | None, root_agent_id: UUID | None = None) -> SimpleNamespace:
    return SimpleNamespace(
        org_context=unit_context(TEST_USER_ID),
        agent_id=str(agent_id) if agent_id else None,
        usage_root_agent_id=str(root_agent_id) if root_agent_id else None,
    )


async def test_guard_is_installed_whenever_a_tier_of_the_subject_has_a_limit(
    db: AsyncSession, agent_id: UUID
) -> None:
    assert await with_usage_guard(["m"], _cfg(agent_id)) == ["m"]  # no limit anywhere
    await set_agent_limit(db, agent_id, "5")
    _, guard = await with_usage_guard(["m"], _cfg(agent_id))
    assert isinstance(guard, UsageLimitGuardMiddleware)
    assert guard.subject == Subject(TEST_TENANT_ID, TEST_ORG_ID, TEST_USER_ID, agent_id)
    assert await with_usage_guard([], SimpleNamespace(org_context=None)) == []


async def test_child_runtime_uses_the_root_agent_tier(db: AsyncSession, agent_id: UUID) -> None:
    child = (await add_agent(db)).id
    await set_agent_limit(db, agent_id, "5")  # the root's limit, not the child's
    (guard,) = await with_usage_guard([], _cfg(child, root_agent_id=agent_id))
    assert isinstance(guard, UsageLimitGuardMiddleware) and guard.subject.agent_id == agent_id
    assert await with_usage_guard([], _cfg(child)) == []  # a child limit-free on its own


async def test_a_failing_lookup_installs_no_guard(
    db: AsyncSession, agent_id: UUID, monkeypatch: pytest.MonkeyPatch
) -> None:
    await set_agent_limit(db, agent_id, "5")

    def broken() -> Any:
        raise RuntimeError("database down")

    monkeypatch.setattr(usage_guard, "async_session", broken)
    assert await with_usage_guard([], _cfg(agent_id)) == []


async def test_run_stops_before_the_next_model_call_once_the_limit_is_reached(
    db: AsyncSession, agent_id: UUID
) -> None:
    await set_agent_limit(db, agent_id, "5")
    subject = subject_of(attribution(agent_id))

    @tool
    async def expensive_step(query: str) -> str:
        """A step whose model calls spend the rest of the budget."""
        async with TestSession() as session:
            await spend(session, "5", agent_id=agent_id)
        return f"done {query}"

    model = FakeToolBindingChatModel(
        responses=[
            AIMessage(
                content="",
                tool_calls=[{"id": "call-1", "name": "expensive_step", "args": {"query": "q"}}],
            ),
            AIMessage(content="never produced"),
        ]
    )
    graph = create_agent(
        model=model,
        tools=[expensive_step],
        middleware=[UsageLimitGuardMiddleware(subject)],
    )
    result = await graph.ainvoke({"messages": [HumanMessage(content="go")]})
    messages = result["messages"]
    assert model.calls == 1
    assert isinstance(messages[-2], ToolMessage) and messages[-2].content == "done q"
    assert isinstance(messages[-1], AIMessage) and messages[-1].content == STOP_MESSAGE
    assert messages[-1].additional_kwargs["moldy_stop"] == {
        "code": "quota_exceeded",
        "scope": "agent",
    }


async def test_a_failing_check_lets_the_call_through(
    db: AsyncSession, agent_id: UUID, monkeypatch: pytest.MonkeyPatch
) -> None:
    def broken() -> Any:
        raise RuntimeError("database down")

    monkeypatch.setattr(usage_guard, "async_session", broken)
    await set_agent_limit(db, agent_id, "5")
    guard = UsageLimitGuardMiddleware(subject_of(attribution(agent_id)))
    assert await guard.abefore_model({"messages": []}, None) is None  # type: ignore[arg-type]
