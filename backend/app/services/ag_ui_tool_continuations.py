"""Recover the issuer of an outstanding tool call across an admitted human continuation."""

from __future__ import annotations

from collections.abc import Collection
from dataclasses import dataclass
from typing import Any
from uuid import UUID

from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.base import BaseCheckpointSaver, CheckpointTuple
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent_runtime.ag_ui_v1 import AgUiV1Projector
from app.agent_runtime.ag_ui_v1_issuance import ToolContinuation
from app.agent_runtime.ag_ui_visibility import ChildVisibility
from app.agent_runtime.chat_steer_checkpoint import run_checkpoint_for_steers
from app.agent_runtime.conversation_checkpointer import loaded_conversation_checkpointer
from app.agent_runtime.protocol_events import protocol_interrupts_from_event
from app.authz.conversation_scope import require_private_conversation
from app.authz.org_context import OrgContext
from app.exceptions import AppError
from app.models.conversation import Conversation
from app.models.conversation_run import ConversationRun
from app.services.ag_ui_commands import load_run_identities
from app.services.ag_ui_journal import read_journal_page
from app.services.ag_ui_read_access import load_child_visibility, require_read_run
from app.services.chat.secrets import collect_conversation_secret_values
from app.tenancy.checkpoint_audit import CHECKPOINT_TENANT_KEY


@dataclass(frozen=True, slots=True)
class _Predecessor:
    run: ConversationRun
    public_id: str
    interrupt_id: str
    visibility: ChildVisibility


def _root_id(config: RunnableConfig | None, thread: str) -> str | None:
    values = (config or {}).get("configurable", {})
    ident = values.get("checkpoint_id")
    if values.get("thread_id") != thread or values.get("checkpoint_ns") != "":
        return None
    return ident if isinstance(ident, str) and ident else None


def _wrote_interrupt(saved: CheckpointTuple, ident: str) -> bool:
    return any(
        getattr(item, "id", None) == ident
        for _, channel, value in saved.pending_writes or ()
        if channel == "__interrupt__" and isinstance(value, (tuple, list))
        for item in value
    )


async def _native_resume_edge(
    saver: BaseCheckpointSaver[Any],
    thread: str,
    child: ConversationRun,
    selected: str,
    tenant: UUID,
) -> bool:
    root: RunnableConfig = {"configurable": {"thread_id": thread, "checkpoint_ns": ""}}
    identity = {"moldy_run_id": str(child.id), CHECKPOINT_TENANT_KEY: str(tenant)}
    # An older native resume has no command receipt. Prove its fork point from
    # the first checkpoint the worker actually committed for this run.
    async for item in saver.alist(root, filter=identity):
        if (
            _root_id(item.config, thread) == item.checkpoint.get("id")
            and all(item.metadata.get(k) == v for k, v in identity.items())
            and _root_id(item.parent_config, thread) == selected
        ):
            return True
    return False


async def _predecessors(
    db: AsyncSession,
    conversation: Conversation,
    context: OrgContext,
    run: ConversationRun,
    checkpoint_id: str | None,
    saver: BaseCheckpointSaver[Any],
) -> list[_Predecessor]:
    chain: list[_Predecessor] = []
    seen = {run.id}
    current = run
    thread = str(conversation.id)
    while current.source == "resume" and current.parent_run_id and current.interrupt_id:
        parent = await require_read_run(
            db, conversation=conversation, context=context, run_id=current.parent_run_id
        )
        if (
            parent.id in seen
            or parent.status != "interrupted"
            or current.agent_id != parent.agent_id
            or current.agent_id != conversation.agent_id
        ):
            break
        identities = await load_run_identities(
            db, context, conversation.id, (current.id, parent.id)
        )
        public_id, _ = identities.get(parent.id, (str(parent.id), None))
        binding = identities.get(current.id)
        if (
            current.id == run.id
            and checkpoint_id is not None
            and (binding is not None and binding[1] != checkpoint_id)
        ):
            break
        saved = await run_checkpoint_for_steers(
            conversation.id, parent.id, saver=saver, tenant_id=context.tenant_id
        )
        if saved is None:
            break
        selected = saved.checkpoint["id"]
        if current.id == run.id and checkpoint_id is not None and selected != checkpoint_id:
            break
        if binding is not None:
            if binding[1] != selected:
                break
        elif not await _native_resume_edge(saver, thread, current, selected, context.tenant_id):
            break
        # A historical task can also have completion writes after its resume;
        # the original, task-owned interrupt record remains in this checkpoint.
        if not _wrote_interrupt(saved, current.interrupt_id):
            break
        seen.add(parent.id)
        chain.append(
            _Predecessor(
                parent,
                public_id,
                current.interrupt_id,
                await load_child_visibility(db, conversation, context, parent.id),
            )
        )
        current = parent
    return chain


async def _issued(
    db: AsyncSession,
    conversation: Conversation,
    predecessor: _Predecessor,
    seeds: tuple[ToolContinuation, ...],
    secrets: Collection[str],
) -> tuple[ToolContinuation, ...]:
    projector = AgUiV1Projector(
        thread_id=str(conversation.id),
        run_id=predecessor.public_id,
        source_run_id=str(predecessor.run.id),
        child_visibility=predecessor.visibility,
        secret_values=secrets,
        tool_continuations=seeds,
        defer_terminal=True,
    )
    after = 0
    boundary: tuple[str | None, int] | None = None
    witnessed = False
    terminal: str | None = None
    while True:
        page = await read_journal_page(
            db,
            conversation=conversation,
            physical_run_id=predecessor.run.id,
            after=after,
            secret_values=secrets,
        )
        shape = (page.generation, page.high_water)
        if page.gap or not page.entries or boundary is not None and boundary != shape:
            return ()
        boundary = shape
        for entry in page.entries:
            if entry.ordinal != after + 1:
                return ()
            after = entry.ordinal
            for event in entry.events:
                projector.project(event)
                if any(
                    s["id"] == predecessor.interrupt_id
                    for s in protocol_interrupts_from_event(event)
                ):
                    witnessed = True
            if entry.terminal_status is not None:
                terminal = entry.terminal_status
        if not page.has_more:
            if witnessed and terminal == "interrupted" and after == page.high_water:
                return projector.unresolved_tool_calls
            return ()


async def read_tool_continuations(
    db: AsyncSession,
    *,
    conversation: Conversation,
    context: OrgContext,
    run: ConversationRun,
    checkpoint_id: str | None,
) -> tuple[ToolContinuation, ...]:
    """Read only public identities issued by the authenticated predecessor chain."""
    if run.source != "resume" or run.parent_run_id is None:
        return ()
    with db.no_autoflush:
        conversation = await require_private_conversation(db, conversation.id, context)
        run = await require_read_run(db, conversation=conversation, context=context, run_id=run.id)
        if conversation.agent_lifecycle_state == "purged":
            return ()
        try:
            saver = await loaded_conversation_checkpointer(conversation, context, db=db)
            chain = await _predecessors(db, conversation, context, run, checkpoint_id, saver)
        except AppError as error:
            if error.status not in (403, 404):
                raise
            return ()
        except (RuntimeError, ValueError, TypeError, AttributeError):
            # An unavailable saver or an old/malformed tuple cannot prove an issuer.
            return ()
        if not chain:
            return ()
        secrets = await collect_conversation_secret_values(db, conversation, context)
        pending: tuple[ToolContinuation, ...] = ()
        for origin in reversed(chain):
            pending = await _issued(db, conversation, origin, pending, secrets)
        if pending:
            for origin in chain:
                await require_read_run(
                    db, conversation=conversation, context=context, run_id=origin.run.id
                )
                if (
                    await load_child_visibility(db, conversation, context, origin.run.id)
                    != origin.visibility
                ):
                    return ()
        return pending
