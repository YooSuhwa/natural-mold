"""An accepted continuation can restore its issuer only on the chosen native branch."""

from uuid import UUID, uuid4

import pytest
from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.base import empty_checkpoint
from langgraph.checkpoint.memory import InMemorySaver

from app.models.conversation import Conversation
from app.services.ag_ui_historical_child_visibility import _original_checkpoint, _Successor
from tests.authz.unit_scope_fixture import unit_context


@pytest.mark.asyncio
async def test_original_requires_scoped_successor_on_the_same_root_lineage() -> None:
    saver = InMemorySaver()
    chat = Conversation(id=uuid4())
    ctx = unit_context(uuid4())
    issuer, successor, other = uuid4(), uuid4(), uuid4()
    root = {"thread_id": str(chat.id), "checkpoint_ns": ""}

    async def write(parent: str | None, run: UUID) -> str:
        config: RunnableConfig = {
            "configurable": {
                **root,
                "moldy_run_id": str(run),
                "moldy_tenant_id": str(ctx.tenant_id),
            }
        }
        if parent is not None:
            config["configurable"]["checkpoint_id"] = parent
        checkpoint = empty_checkpoint()
        await saver.aput(config, checkpoint, {}, {})
        return checkpoint["id"]

    original = await write(None, issuer)
    chosen = await write(original, successor)
    other_tip = await write(original, other)
    receipt = _Successor(successor, "completed", original)
    assert await _original_checkpoint(saver, chat, ctx, issuer, receipt) is None
    chat.active_branch_checkpoint_id = chosen
    confirmed = await _original_checkpoint(saver, chat, ctx, issuer, receipt)
    assert confirmed is not None
    assert confirmed.saved.checkpoint["id"] == original and confirmed.latest_root_id == other_tip
    assert await _original_checkpoint(saver, chat, ctx, other, receipt) is None
    foreign_receipt = _Successor(other, "completed", original)
    assert await _original_checkpoint(saver, chat, ctx, issuer, foreign_receipt) is None
