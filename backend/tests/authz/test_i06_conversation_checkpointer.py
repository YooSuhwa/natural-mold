"""I-06 / plan 8.8: the conversation checkpointer gate blocks before the pool."""

from __future__ import annotations

import ast
import uuid
from pathlib import Path

import pytest

from app.agent_runtime.conversation_checkpointer import conversation_checkpointer
from app.authz.org_context import OrgContext
from app.database import SessionTenant
from app.exceptions import NotFoundError
from tests.authz.agent_execution_runtime_fixture import seed_runtime_run
from tests.authz.unit_scope_fixture import TEST_ORG_ID, TEST_TENANT_ID
from tests.conftest import TEST_USER_ID, TestSession

APP = Path(__file__).resolve().parents[2] / "app"
SAVER = object()
# Raw ``get_checkpointer()`` reads that do not pass the gate yet (I-06 open item):
# a new direct read fails here until it is gated or reviewed into this list.
UNGATED_CHECKPOINTER_SITES = {
    "agent_runtime/assistant/assistant_agent.py": 1,  # assistant thread, not a conversation
    "agent_runtime/checkpointer.py": 1,  # create_lease_checkpointer (run-owned lease)
    "agent_runtime/langgraph_pending_inputs.py": 1,
    "agent_runtime/prepared_run_inputs.py": 1,
    "agent_runtime/runtime_agent_preparation.py": 1,
    "routers/conversation_agent_protocol_resume_redaction.py": 1,
    "routers/conversation_agent_protocol_state_snapshot.py": 1,
    "routers/conversation_branch_entries.py": 3,
    "routers/conversation_pinned_summaries.py": 1,
    "routers/conversation_protocol_fork.py": 1,
    "routers/e2e_mcp_apps_helpers.py": 1,  # e2e-only helper
    "services/agent_purge_cleanup.py": 1,
    "services/agent_purge_service.py": 1,
    "services/builder_session_service.py": 2,  # builder thread, not a conversation
    "services/chat/attachments.py": 1,
    "services/chat/message_membership.py": 1,
    "services/chat_quote_context.py": 1,
    "services/chat_resource_context.py": 1,
    "services/conversation_branch_service.py": 1,
    "services/conversation_run_worker.py": 1,
    # also ungated: conversation_run_delivery (worker_attr lookup), conversation_protocol_history
    # (factory default) — the AST pin sees direct calls only.
}
GATED_SITES = {"routers/conversation_messages.py", "services/chat/messages.py"}


def _saver() -> object:
    return SAVER


@pytest.fixture
async def conversation_id() -> uuid.UUID:
    _run, _agent, conversation = await seed_runtime_run()
    return conversation.id


def _own() -> OrgContext:
    return OrgContext(org_id=TEST_ORG_ID, tenant_id=TEST_TENANT_ID, user_id=TEST_USER_ID)


async def test_own_conversation_returns_the_saver(conversation_id: uuid.UUID) -> None:
    async with TestSession() as db:
        saver = await conversation_checkpointer(str(conversation_id), _own(), db=db, saver=_saver)
    assert saver is SAVER


@pytest.mark.parametrize(
    "ctx",
    [
        SessionTenant(uuid.uuid4(), TEST_USER_ID),  # another company
        OrgContext(org_id=uuid.uuid4(), tenant_id=TEST_TENANT_ID, user_id=TEST_USER_ID),
        OrgContext(org_id=TEST_ORG_ID, tenant_id=TEST_TENANT_ID, user_id=uuid.uuid4()),
    ],
    ids=["other-company", "other-organization", "other-owner"],
)
async def test_foreign_context_is_denied_before_the_pool(
    conversation_id: uuid.UUID, ctx: object
) -> None:
    calls: list[str] = []

    def saver() -> object:
        calls.append("pool")
        return SAVER

    async with TestSession() as db:
        with pytest.raises(NotFoundError):
            await conversation_checkpointer(str(conversation_id), ctx, db=db, saver=saver)  # type: ignore[arg-type]
    assert calls == []


@pytest.mark.parametrize(
    "thread_id", ["not-a-uuid", "00000000-0000-0000-0000-0000000000f6"], ids=["text", "missing"]
)
async def test_unknown_thread_is_the_same_404(thread_id: str) -> None:
    async with TestSession() as db:
        with pytest.raises(NotFoundError):
            await conversation_checkpointer(thread_id, _own(), db=db, saver=_saver)


async def test_company_only_scope_passes_for_its_own_conversation(
    conversation_id: uuid.UUID,
) -> None:
    async with TestSession() as db:
        assert (
            await conversation_checkpointer(
                str(conversation_id), SessionTenant(TEST_TENANT_ID), db=db, saver=_saver
            )
            is SAVER
        )


def _calls(name: str) -> dict[str, int]:
    found: dict[str, int] = {}
    for path in sorted(APP.rglob("*.py")):
        for node in ast.walk(ast.parse(path.read_text())):
            if isinstance(node, ast.Call):
                func = node.func
                callee = func.id if isinstance(func, ast.Name) else getattr(func, "attr", "")
                if callee == name:
                    rel = path.relative_to(APP).as_posix()
                    found[rel] = found.get(rel, 0) + 1
    return found


def test_direct_checkpointer_reads_are_pinned() -> None:
    assert _calls("get_checkpointer") == UNGATED_CHECKPOINTER_SITES


def test_message_reads_pass_the_gate() -> None:
    assert set(_calls("conversation_checkpointer")) >= GATED_SITES
