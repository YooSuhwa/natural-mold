"""Persist the same private deferred run binding left by a committed fork command."""

from dataclasses import dataclass
from uuid import UUID, uuid4

from langchain_core.messages import HumanMessage
from langgraph.checkpoint.base import empty_checkpoint
from langgraph.checkpoint.memory import InMemorySaver
from sqlalchemy.ext.asyncio import AsyncSession

from app.authz.org_context import OrgContext
from app.models.ag_ui_command_receipt import AgUiCommandReceipt
from app.models.conversation_run import ConversationRun
from app.services.ag_ui_deferred_delivery import seal_ag_ui_delivery
from app.services.agent_execution_owner import OwnedExecutionRequest, accept_owned_execution
from app.services.chat_service import create_conversation
from app.tenancy.checkpoint_audit import run_configurable
from tests.conftest import make_agent, make_model


@dataclass(frozen=True, slots=True)
class DeferredCase:
    context: OrgContext
    agent_id: UUID
    conversation_id: UUID
    public_run_id: str
    run_id: UUID
    checkpoint_id: str
    receipt: AgUiCommandReceipt


async def seed_deferred(
    db: AsyncSession, context: OrgContext, *, saver: InMemorySaver | None = None
) -> DeferredCase:
    model = await make_model(db, provider="e2e_scripted", model_name=f"deferred-{uuid4().hex}")
    agent = await make_agent(
        db,
        user_id=context.user_id,
        tenant_id=context.tenant_id,
        org_id=context.org_id,
        model_id=model.id,
    )
    conversation = await create_conversation(db, agent.id, context=context)
    checkpoint = empty_checkpoint()
    checkpoint_id = checkpoint["id"]
    if saver is not None:
        configurable = run_configurable(str(conversation.id), uuid4(), context.tenant_id)
        configurable["checkpoint_ns"] = ""
        checkpoint["channel_values"]["messages"] = [
            HumanMessage(id="first-input", content="Test turn")
        ]
        checkpoint["channel_versions"]["messages"] = 1
        await saver.aput({"configurable": configurable}, checkpoint, {}, {"messages": 1})
    conversation.active_branch_checkpoint_id = checkpoint_id
    await db.commit()
    accepted = await accept_owned_execution(
        db,
        OwnedExecutionRequest(
            conversation_id=conversation.id,
            root_agent_id=agent.id,
            context=context,
            source="chat",
            run_source="regenerate",
            input_preview=None,
        ),
    )
    run = await db.get(ConversationRun, accepted.run_id)
    assert run is not None
    receipt = await stage_receipt(
        db,
        context,
        run,
        checkpoint_id,
        {"expected_head": checkpoint_id, "input": {"messages": []}, "attachment_ids": []},
    )
    return DeferredCase(
        context, agent.id, conversation.id, receipt.public_run_id, run.id, checkpoint_id, receipt
    )


async def stage_receipt(
    db: AsyncSession, context: OrgContext, run: ConversationRun, checkpoint_id: str, payload: object
) -> AgUiCommandReceipt:
    public_id = uuid4().hex
    ciphertext = await seal_ag_ui_delivery(
        db,
        context=context,
        conversation_id=run.conversation_id,
        public_run_id=public_id,
        run_id=run.id,
        source=run.source,
        parent_run_id=run.parent_run_id,
        checkpoint_id=checkpoint_id,
        payload=payload,
    )
    receipt = AgUiCommandReceipt(
        tenant_id=context.tenant_id,
        org_id=context.org_id,
        user_id=context.user_id,
        conversation_id=run.conversation_id,
        public_run_id=public_id,
        request_digest="a" * 64,
        projection_version="1",
        run_id=run.id,
        checkpoint_id=checkpoint_id,
        delivery_encrypted=ciphertext,
    )
    db.add(receipt)
    await db.flush()
    return receipt
