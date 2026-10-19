"""F-14 (8.7, Q8): spend is booked to the requester inside the run's organization.

Chat bills the caller, triggers the trigger owner and Agent API the key owner — all the
run context's user — even when the agent belongs to someone else. Aggregates are keyed
``(org_id, date, target)`` and the Assistant books the caller's tokens.
"""

from __future__ import annotations

import uuid
from datetime import date
from decimal import Decimal
from typing import Any

import pytest
from sqlalchemy import select
from sqlalchemy.orm import load_only

from app.agent_runtime.agent_stream_hooks import _hook_ctx_for_agent
from app.agent_runtime.runtime_config import AgentConfig
from app.authz.org_context import OrgContext
from app.hooks import HookResult, SpendHook
from app.models.conversation_run import ConversationRun
from app.models.daily_spend_user import DailySpendUser
from app.services import assistant_service
from app.services.spend_writer import DailySpendUpdateQueue, SpendEntry
from app.tenancy.attribution import (
    AttributionNotLoadedError,
    attribute_child,
    attribute_run,
    usage_columns,
)
from tests.conftest import TestSession

pytestmark = pytest.mark.asyncio
OWNER, REQUESTER = uuid.uuid4(), uuid.uuid4()
ORG, OTHER_ORG, TENANT = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()


class _Queue:
    def __init__(self) -> None:
        self.entries: list[SpendEntry] = []

    def add(self, entry: SpendEntry) -> None:
        self.entries.append(entry)


def _cfg(requester: uuid.UUID) -> AgentConfig:
    # Trigger/API configs carry the agent owner as ``user_id``; the context is the requester.
    return AgentConfig(
        org_context=OrgContext(org_id=ORG, tenant_id=TENANT, user_id=requester),
        provider="openai",
        model_name="m",
        api_key=None,
        base_url=None,
        system_prompt="",
        tools_config=[],
        thread_id="t",
        user_id=str(OWNER),
        agent_id=str(uuid.uuid4()),
    )


async def test_spend_hook_bills_the_run_requester_in_the_run_org(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    queue = _Queue()
    monkeypatch.setattr("app.hooks.builtin.spend_hook.spend_queue", queue)
    ctx = _hook_ctx_for_agent(_cfg(REQUESTER))
    assert ctx is not None
    await SpendHook().async_post_call_hook(ctx, HookResult(duration_ms=1, tokens_in=3))
    [entry] = queue.entries
    assert (entry.user_id, entry.org_id, entry.tenant_id) == (REQUESTER, ORG, TENANT)
    assert entry.agent_id == ctx.agent_id


async def test_aggregates_split_by_organization_and_accumulate_within_one() -> None:
    queue = DailySpendUpdateQueue()
    queue._session_factory = TestSession  # type: ignore[assignment]

    def entry(org: uuid.UUID) -> SpendEntry:
        day = date(2026, 10, 1)
        return SpendEntry(day, REQUESTER, 2, 1, Decimal("0.5"), org_id=org, tenant_id=TENANT)

    await queue._flush_batch([entry(ORG), entry(OTHER_ORG)])
    await queue._flush_batch([entry(ORG)])
    async with TestSession() as db:
        rows = (await db.scalars(select(DailySpendUser))).all()
    totals = {row.org_id: (row.total_tokens_in, row.request_count) for row in rows}
    assert totals == {ORG: (4, 2), OTHER_ORG: (2, 1)}


async def test_assistant_books_the_callers_tokens(monkeypatch: pytest.MonkeyPatch) -> None:
    queue = _Queue()
    monkeypatch.setattr(assistant_service, "spend_queue", queue)
    context = OrgContext(org_id=ORG, tenant_id=TENANT, user_id=REQUESTER)
    assistant_service.book_assistant_usage(context, {})
    usage: dict[str, Any] = {"prompt_tokens": 5, "completion_tokens": 2}
    assistant_service.book_assistant_usage(context, usage)
    [entry] = queue.entries
    assert (entry.user_id, entry.org_id, entry.agent_id) == (REQUESTER, ORG, None)
    assert (entry.tokens_in, entry.tokens_out) == (5, 2)


class _Agent:
    def __init__(self, owner: uuid.UUID, mode: str) -> None:
        self.id, self.user_id, self.identity_mode = uuid.uuid4(), owner, mode


@pytest.mark.parametrize("source", ["trigger", "api"])
async def test_callerless_runs_act_as_owners_even_for_per_user_children(source: str) -> None:
    context = OrgContext(org_id=ORG, tenant_id=TENANT, user_id=REQUESTER)
    root = attribute_run(source, context, _Agent(OWNER, "per_user"))  # type: ignore[arg-type]
    child_owner = uuid.uuid4()
    child = attribute_child(root, _Agent(child_owner, "per_user"))  # type: ignore[arg-type]
    assert (root.caller_user_id, root.on_behalf_of_user_id) == (REQUESTER, OWNER)
    assert (child.caller_user_id, child.on_behalf_of_user_id) == (REQUESTER, child_owner)
    assert (child.run_source, child.invoker_type) == ("SUBAGENT", "subagent")


async def test_assistant_books_usage_even_when_the_stream_is_closed_early(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    queue = _Queue()
    monkeypatch.setattr(assistant_service, "spend_queue", queue)

    async def build(*_args: object) -> object:
        return object()

    async def stream(*_args: object, usage_sink: dict[str, Any], **_kwargs: object):
        usage_sink.update(prompt_tokens=4, completion_tokens=1)
        yield "event: a\n\n"
        yield "event: b\n\n"

    monkeypatch.setattr(assistant_service, "build_assistant_agent", build)
    monkeypatch.setattr(assistant_service, "stream_agent_response", stream)
    context = OrgContext(org_id=ORG, tenant_id=TENANT, user_id=REQUESTER)
    generator = assistant_service.stream_assistant_message(None, uuid.uuid4(), context, "t", "hi")  # type: ignore[arg-type]
    assert await anext(generator) == "event: a\n\n"
    await generator.aclose()  # client disconnected mid-stream
    [entry] = queue.entries
    assert (entry.user_id, entry.tokens_in, entry.tokens_out) == (REQUESTER, 4, 1)


async def test_usage_columns_refuse_a_run_without_requester_or_org() -> None:
    async with TestSession() as db:
        run = ConversationRun(
            conversation_id=uuid.uuid4(), user_id=REQUESTER, source="chat", status="queued"
        )
        db.add(run)
        await db.commit()
        partial = await db.scalar(
            select(ConversationRun)
            .where(ConversationRun.id == run.id)
            .options(load_only(ConversationRun.id, ConversationRun.user_id))
            .execution_options(populate_existing=True)
        )
    with pytest.raises(AttributionNotLoadedError, match="org_id"):
        usage_columns(partial)
