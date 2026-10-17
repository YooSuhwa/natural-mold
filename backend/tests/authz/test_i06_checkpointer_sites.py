"""I-06 / plan 8.8: representative checkpointer sites deny before the pool.

Fork, state snapshot, branch entries and pinned summaries pass the conversation
gate; builder and assistant threads pass their own owning-row gates. A foreign
company, organization or owner is a 404 and the checkpointer pool is never read.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable

import pytest

from app.agent_runtime import checkpointer as checkpointer_module
from app.agent_runtime.thread_checkpointers import assistant_checkpointer
from app.authz.builder_scope import builder_checkpointer
from app.authz.org_context import OrgContext
from app.database import SessionTenant, tenant_session
from app.exceptions import NotFoundError
from app.models.agent import Agent
from app.models.builder_session import BuilderSession
from app.models.conversation import Conversation
from app.routers import conversation_agent_protocol_state_snapshot as snapshot_module
from app.routers import conversation_pinned_summaries
from app.routers.conversation_protocol_fork import _fork_overwrite_input
from app.schemas.builder import BuilderStatus
from app.services.conversation_branch_service import resolve_branch_checkpoint
from tests.authz.agent_execution_runtime_fixture import seed_runtime_run
from tests.authz.unit_scope_fixture import TEST_ORG_ID, TEST_TENANT_ID
from tests.conftest import TEST_USER_ID, TestSession

FOREIGN = [
    OrgContext(org_id=TEST_ORG_ID, tenant_id=uuid.uuid4(), user_id=TEST_USER_ID),
    OrgContext(org_id=uuid.uuid4(), tenant_id=TEST_TENANT_ID, user_id=TEST_USER_ID),
    OrgContext(org_id=TEST_ORG_ID, tenant_id=TEST_TENANT_ID, user_id=uuid.uuid4()),
]
FOREIGN_IDS = ["other-company", "other-organization", "other-owner"]
SAVER = object()


@pytest.fixture
async def seeded() -> tuple[Agent, Conversation]:
    _run, agent, conversation = await seed_runtime_run()
    return agent, conversation


@pytest.fixture
def pool(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    calls: list[str] = []

    def saver() -> object:
        calls.append("pool")
        return SAVER

    monkeypatch.setattr(checkpointer_module, "get_checkpointer", saver)
    monkeypatch.setattr(snapshot_module, "get_checkpointer", saver)
    monkeypatch.setattr(conversation_pinned_summaries, "get_checkpointer", saver)
    return calls


@pytest.mark.parametrize("ctx", FOREIGN, ids=FOREIGN_IDS)
async def test_fork_overwrite_input_denies_a_foreign_context(
    seeded: tuple[Agent, Conversation], pool: list[str], ctx: OrgContext
) -> None:
    async with TestSession() as db:
        with pytest.raises(NotFoundError):
            await _fork_overwrite_input(
                db=db,
                context=ctx,
                conversation_id=seeded[1].id,
                checkpoint_id="checkpoint-1",
                append_messages=[],
            )
    assert pool == []


@pytest.mark.parametrize("ctx", FOREIGN, ids=FOREIGN_IDS)
@pytest.mark.parametrize("with_session", [True, False], ids=["session", "loaded-row"])
async def test_state_snapshot_denies_a_foreign_context(
    seeded: tuple[Agent, Conversation], pool: list[str], ctx: OrgContext, with_session: bool
) -> None:
    async with TestSession() as db:
        with pytest.raises(NotFoundError):
            await snapshot_module.load_thread_state_snapshot(
                seeded[1], ctx, db if with_session else None
            )
    assert pool == []


@pytest.mark.parametrize("ctx", FOREIGN, ids=FOREIGN_IDS)
async def test_branch_entry_resolution_denies_a_foreign_context(
    seeded: tuple[Agent, Conversation], pool: list[str], ctx: OrgContext
) -> None:
    async with TestSession() as db:
        with pytest.raises(NotFoundError):
            await resolve_branch_checkpoint(seeded[1].id, uuid.uuid4(), db=db, context=ctx)
    assert pool == []


@pytest.mark.parametrize("ctx", FOREIGN, ids=FOREIGN_IDS)
async def test_pinned_summary_sources_deny_a_foreign_context(
    seeded: tuple[Agent, Conversation], pool: list[str], ctx: OrgContext
) -> None:
    async with TestSession() as db:
        with pytest.raises(NotFoundError):
            await conversation_pinned_summaries._load_sources(  # noqa: SLF001
                seeded[1].id, None, db=db, context=ctx
            )
    assert pool == []


def _recording(calls: list[str]) -> Callable[[], object]:
    def saver() -> object:
        calls.append("pool")
        return SAVER

    return saver


async def _builder_session(seeded: tuple[Agent, Conversation]) -> uuid.UUID:
    async with TestSession() as db:
        row = BuilderSession(
            user_id=TEST_USER_ID,
            user_request="i06 builder gate",
            status=BuilderStatus.BUILDING,
            org_id=TEST_ORG_ID,
            tenant_id=TEST_TENANT_ID,
        )
        db.add(row)
        await db.commit()
        return row.id


async def test_builder_thread_is_gated_by_its_session_row_and_company(
    seeded: tuple[Agent, Conversation],
) -> None:
    session_id = await _builder_session(seeded)
    calls: list[str] = []
    own = SessionTenant(TEST_TENANT_ID, TEST_USER_ID)
    async with tenant_session(own, maker=TestSession) as db:
        assert await builder_checkpointer(db, session_id, TEST_USER_ID, saver=_recording(calls))
        assert calls == ["pool"]
        with pytest.raises(NotFoundError):  # another owner
            await builder_checkpointer(db, session_id, uuid.uuid4(), saver=_recording(calls))
        with pytest.raises(NotFoundError):  # unknown session
            await builder_checkpointer(db, uuid.uuid4(), TEST_USER_ID, saver=_recording(calls))
    other_company = SessionTenant(uuid.uuid4(), TEST_USER_ID)
    for scope in (other_company, None):  # another company / unbound fails closed
        async with tenant_session(scope, maker=TestSession) as db:
            with pytest.raises(NotFoundError):
                await builder_checkpointer(db, session_id, TEST_USER_ID, saver=_recording(calls))
    assert calls == ["pool"]


async def test_assistant_thread_is_gated_by_its_agent_and_company(
    seeded: tuple[Agent, Conversation],
) -> None:
    agent_id = seeded[0].id
    calls: list[str] = []
    own = OrgContext(org_id=TEST_ORG_ID, tenant_id=TEST_TENANT_ID, user_id=TEST_USER_ID)
    async with TestSession() as db:
        for thread in (f"assistant_{agent_id}", f"assistant_{agent_id}_tab-1"):
            assert await assistant_checkpointer(db, thread, agent_id, own, saver=_recording(calls))
        denied = [
            (f"assistant_{agent_id}", agent_id, SessionTenant(uuid.uuid4(), TEST_USER_ID)),
            (f"assistant_{uuid.uuid4()}", agent_id, own),  # another agent's thread
            (f"assistant_{agent_id}x", agent_id, own),  # prefix, not the agent's thread
            (str(seeded[1].id), agent_id, own),  # a conversation thread
            (f"assistant_{agent_id}", uuid.uuid4(), own),  # unknown agent
        ]
        for thread, target, ctx in denied:
            with pytest.raises(NotFoundError):
                await assistant_checkpointer(db, thread, target, ctx, saver=_recording(calls))
    assert calls == ["pool", "pool"]
