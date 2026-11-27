"""Read the active graph's unanswered interrupts for a scoped physical chat run.

Both snapshots and resume admission use this read. A CheckpointTuple contains an
append/replace log of writes. Only the SDK's task status distinguishes an unanswered
question from a completed parallel task whose interrupt remains in that log.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any
from uuid import UUID

from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.base import BaseCheckpointSaver, CheckpointTuple
from langgraph.pregel._task_status import read_task_statuses
from langgraph.types import _DEFAULT_INTERRUPT_ID, Interrupt
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent_runtime.ag_ui_v1_interrupt_payload import interrupt_fields
from app.agent_runtime.conversation_checkpointer import conversation_checkpointer, deferred_saver
from app.agent_runtime.protocol_events import ProtocolInterrupt as ThreadInterrupt
from app.agent_runtime.streaming import _interrupt_to_standard_chunk
from app.approvals.service import approval_actions, args_digest, aware
from app.authz.conversation_steer_scope import steer_run_actor_scope
from app.authz.org_context import OrgContext
from app.authz.scoping import private_scope_predicate
from app.models.approval_request import ApprovalRequest
from app.models.conversation import INDEPENDENT_JOB_SOURCE, Conversation
from app.models.conversation_run import ConversationRun
from app.models.independent_chat_job import IndependentChatJob
from app.models.message_event import MessageEvent
from app.services import trace_storage
from app.services.ag_ui_legacy_child_binding import LegacyChildBinding, read_legacy_child_bindings
from app.services.independent_job_runs import can_read_independent_child_run
from app.tenancy.checkpoint_audit import CHECKPOINT_TENANT_KEY

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class PendingCheckpoint:
    checkpoint_id: str
    interrupts: list[ThreadInterrupt]
    expires_at: dict[str, datetime]
    native_kinds: dict[str, str] = field(default_factory=dict)
    approval_run_ids: dict[str, tuple[UUID, ...]] = field(default_factory=dict)
    root_task_ids: dict[str, str] = field(default_factory=dict)
    legacy_child_bindings: dict[str, LegacyChildBinding | None] = field(default_factory=dict)
    # Resume uses the command envelope in interrupts. Readers also need the
    # typed, public fields of questions and connection prompts issued by the graph.
    native_prompt_values: dict[str, dict[str, Any]] = field(default_factory=dict)


class CheckpointUnavailable(RuntimeError):
    """The native saver failed a read; an admitted worker may retry."""


def _unavailable(exc: Exception, run_id: UUID, retry: bool) -> None:
    logger.warning("AG-UI checkpoint unavailable for run %s (%s)", run_id, type(exc).__name__)
    if retry:
        raise CheckpointUnavailable("Unable to read chat checkpoint") from exc


def _root_id(config: RunnableConfig | None, thread_id: str) -> str | None:
    values = (config or {}).get("configurable", {})
    if values.get("thread_id") != thread_id or values.get("checkpoint_ns") != "":
        return None
    ident = values.get("checkpoint_id")
    return ident if isinstance(ident, str) and ident else None


async def _selected_head(
    saver: BaseCheckpointSaver[Any], thread_id: str, tenant_id: UUID, selected: str | None
) -> CheckpointTuple | None:
    root: RunnableConfig = {"configurable": {"thread_id": thread_id, "checkpoint_ns": ""}}
    head = current = await saver.aget_tuple(root)
    seen: set[str] = set()
    expected = None
    while current is not None:
        ident = _root_id(current.config, thread_id)
        tenant = current.metadata.get(CHECKPOINT_TENANT_KEY)
        if (
            not ident
            or ident != current.checkpoint.get("id")
            or ident in seen
            or (expected is not None and ident != expected)
            or tenant not in (None, str(tenant_id))
        ):
            return None
        if selected is None or ident == selected:
            return head
        seen.add(ident)
        expected = _root_id(current.parent_config, thread_id)
        if expected is None:
            return None
        # Resuming an older sibling in LangGraph would replay it as a fork, including
        # already finished tasks. Only a selection on this live head's path can resume.
        current = await saver.aget_tuple(
            {"configurable": {**root["configurable"], "checkpoint_id": expected}}
        )
    return None


def _interrupt_value(ident: str, raw: Any) -> tuple[str, dict[str, Any]]:
    if isinstance(raw, dict):
        native = raw.get("type")
        if native == "workflow_wait":
            return "workflow_wait", {"type": "workflow_wait"}
        action_shape = "action_requests" in raw or "review_configs" in raw
        if (native is None and action_shape) or (
            native in ("ask_user", "auth_required") and not action_shape
        ):
            standard = _interrupt_to_standard_chunk(ident, raw)
            if standard is not None:
                return native or "approval", {**standard, "interrupt_id": ident}
    return "unknown", {"type": "unknown"}


def _unanswered(saved: CheckpointTuple) -> PendingCheckpoint | None:
    items: list[ThreadInterrupt] = []
    kinds: dict[str, str] = {}
    tasks: dict[str, str] = {}
    prompts: dict[str, dict[str, Any]] = {}
    for task_id, task in read_task_statuses(saved.pending_writes or ()).items():
        for interrupt in task.pending_interrupts:
            if not isinstance(interrupt, Interrupt):
                return None
            ident = interrupt.id
            if not isinstance(ident, str) or ident in ("", _DEFAULT_INTERRUPT_ID) or ident in kinds:
                return None
            tasks[ident] = task_id
            kind, value = _interrupt_value(ident, interrupt.value)
            kinds[ident] = kind
            if kind in ("ask_user", "auth_required"):
                fields = interrupt_fields(interrupt.value)
                if fields.get("reason") == f"moldy.{kind}":
                    prompts[ident] = fields["metadata"]["moldy"]
            items.append({"id": ident, "value": value, "ns": []})
    return PendingCheckpoint(
        saved.checkpoint["id"], items, {}, kinds, root_task_ids=tasks, native_prompt_values=prompts
    )


async def _run_lineage(
    db: AsyncSession, parent_id: UUID, conversation_id: UUID, agent_id: UUID, context: OrgContext
) -> set[UUID]:
    # A partial parallel resume can leave an original approval on the same checkpoint,
    # even when a later physical run recorded a second approval for that interrupt.
    columns = (ConversationRun.id, ConversationRun.parent_run_id, ConversationRun.source)
    scoped = select(*columns).where(
        ConversationRun.conversation_id == conversation_id,
        ConversationRun.agent_id == agent_id,
        ConversationRun.status == "interrupted",
        steer_run_actor_scope(context),
    )
    seed = scoped.where(ConversationRun.id == parent_id).cte(recursive=True)
    chain = seed.union(
        scoped.where(seed.c.source == "resume", ConversationRun.id == seed.c.parent_run_id)
    )
    rows = {r.id: r for r in (await db.execute(select(chain))).all()}
    seen: set[UUID] = set()
    cursor: UUID | None = parent_id
    while cursor is not None and cursor in rows and cursor not in seen:
        row = rows[cursor]
        seen.add(cursor)
        if row.source != "resume":
            return seen if row.parent_run_id is None else set()
        cursor = row.parent_run_id
    return set()


def _restore_consent_offer(item: ThreadInterrupt, journal: Any) -> None:
    """The run stream annotates this single eligible offer after graph writes."""
    from app.agent_runtime.skill_builder.tools import SESSION_CONSENT_ELIGIBLE_TOOLS

    native = item["value"]
    if not isinstance(journal, dict):
        return
    actions, configs = native.get("action_requests"), native.get("review_configs")
    shown_actions, shown_configs = journal.get("action_requests"), journal.get("review_configs")
    if not isinstance(actions, list) or not isinstance(configs, list):
        return
    if not isinstance(shown_actions, list) or not isinstance(shown_configs, list):
        return
    if len(actions) != len(shown_actions) or len(configs) != len(shown_configs):
        return
    recovered = list(configs)
    for i, (action, shown, config, offer) in enumerate(
        zip(actions, shown_actions, configs, shown_configs, strict=False)
    ):
        if not isinstance(action, dict) or not isinstance(shown, dict):
            continue
        if not isinstance(config, dict) or not isinstance(offer, dict):
            continue
        name = action.get("name")
        if (
            isinstance(name, str)
            and name in SESSION_CONSENT_ELIGIBLE_TOOLS
            and name == shown.get("name") == config.get("action_name") == offer.get("action_name")
            and "args" in action
            and action["args"] == shown.get("args")
            and action.get("id") == shown.get("id")
            and offer.get("session_consent_eligible") is True
        ):
            recovered[i] = {**config, "session_consent_eligible": True}
    item["value"] = {**native, "review_configs": recovered}


async def _journal_namespaces(
    db: AsyncSession, conversation_id: UUID, run_id: UUID, pending: PendingCheckpoint
) -> dict[str, list[str]] | None:
    slots = {item["id"]: item for item in pending.interrupts}
    record = await db.scalar(
        select(MessageEvent).where(
            MessageEvent.assistant_msg_id == str(run_id),
            MessageEvent.conversation_id == conversation_id,
        )
    )
    namespaces: dict[str, list[str]] = {}
    if record is None:
        return namespaces
    for event in await trace_storage.load_events(db, record):
        if (event.get("method"), event.get("run_id"), event.get("thread_id")) != (
            "input.requested",
            str(run_id),
            str(conversation_id),
        ):
            continue
        data = event.get("data")
        if not isinstance(data, dict):
            continue
        ident = data.get("interrupt_id")
        if not isinstance(ident, str) or ident not in slots:
            continue
        ns = data.get("namespace") or event.get("namespace") or []
        if not isinstance(ns, list) or not all(isinstance(n, str) for n in ns):
            return None
        # A child and the root can both report its interrupt. An empty root namespace
        # does not erase the child attribution. Conflicting child origins are ambiguous.
        if ns and namespaces.get(ident) and ns != namespaces[ident]:
            return None
        namespaces[ident] = ns or namespaces.get(ident, [])
        _restore_consent_offer(slots[ident], data.get("payload"))
    return namespaces


def _narrow_review_config(item: ThreadInterrupt, recorded: Any) -> bool:
    """Apply each recorded decision limit without editing the saver's native payload."""
    value = item["value"]
    actions, configs = value.get("action_requests"), value.get("review_configs")
    if (
        not isinstance(recorded, list)
        or not isinstance(actions, list)
        or not isinstance(configs, list)
    ):
        return False
    if len(recorded) != len(actions):
        return False
    narrowed = []
    for index, (action, rule) in enumerate(zip(actions, recorded, strict=True)):
        name = action.get("name")
        if not isinstance(name, str) or not isinstance(rule, dict):
            return False
        # Standard middleware may reuse one review config for several calls of a tool.
        config = configs[index] if index < len(configs) else None
        if not isinstance(config, dict) or config.get("action_name") != name:
            config = next(
                (r for r in configs if isinstance(r, dict) and r.get("action_name") == name), None
            )
        if not isinstance(config, dict):
            return False
        allowed = config.get("allowed_decisions")
        limit = rule.get("allowed_decisions")
        if not isinstance(allowed, list) or not isinstance(limit, list):
            return False
        narrowed.append(
            {
                **config,
                "allowed_decisions": [d for d in allowed if isinstance(d, str) and d in limit],
            }
        )
    item["value"] = {**value, "review_configs": narrowed}
    return True


async def _check_approval_rows(
    db: AsyncSession,
    pending: PendingCheckpoint,
    lineage: set[UUID],
    conversation_id: UUID,
    context: OrgContext,
) -> bool:
    items = {item["id"]: item for item in pending.interrupts}
    rows = await db.execute(
        select(
            ApprovalRequest.run_id,
            ApprovalRequest.interrupt_id,
            ApprovalRequest.kind,
            ApprovalRequest.conversation_id,
            ApprovalRequest.subject_user_id,
            ApprovalRequest.decider_user_id,
            ApprovalRequest.tenant_id,
            ApprovalRequest.org_id,
            ApprovalRequest.args_digest,
            ApprovalRequest.expires_at,
            ApprovalRequest.namespace,
            ApprovalRequest.actions,
        )
        .where(ApprovalRequest.run_id.in_(lineage), ApprovalRequest.interrupt_id.in_(items))
        .order_by(ApprovalRequest.run_id)
    )
    for row in rows:
        item = items[row.interrupt_id]
        if (
            row.kind != "tool"
            or row.conversation_id != conversation_id
            or row.subject_user_id != context.user_id
            or row.decider_user_id != context.user_id
            or row.tenant_id != context.tenant_id
            or row.org_id != context.org_id
            or row.run_id is None
            or approval_actions(item["value"]) is None
            or args_digest(item["value"]) != row.args_digest
            or not _narrow_review_config(item, row.actions)
        ):
            return False
        ns = row.namespace
        if not isinstance(ns, list) or not all(isinstance(n, str) for n in ns):
            return False
        if ns:
            if item["ns"] and item["ns"] != ns:
                return False
            item["ns"] = list(ns)
        ident = row.interrupt_id
        deadline = aware(row.expires_at)
        pending.expires_at[ident] = min(pending.expires_at.get(ident, deadline), deadline)
        pending.approval_run_ids[ident] = (*pending.approval_run_ids.get(ident, ()), row.run_id)
    return True


async def read_pending_checkpoint(
    db: AsyncSession,
    *,
    conversation: Conversation,
    context: OrgContext,
    parent_run: ConversationRun,
    raise_on_unavailable: bool = False,
) -> PendingCheckpoint | None:
    """Read an authenticated live root, even after its resume child has been admitted.

    None means unavailable, unowned or stale. An authenticated idle root returns an
    empty list. No product state is changed; decision/expiry claims are separate.
    """
    with db.no_autoflush:
        row = (
            await db.execute(
                select(
                    Conversation.active_branch_checkpoint_id,
                    Conversation.source,
                    Conversation.agent_id,
                    ConversationRun.source,
                    ConversationRun.parent_run_id,
                    ConversationRun.interrupt_id,
                )
                .join(ConversationRun, ConversationRun.conversation_id == Conversation.id)
                .where(
                    Conversation.id == conversation.id,
                    private_scope_predicate(Conversation, context),
                    Conversation.agent_lifecycle_state != "purged",
                    ConversationRun.id == parent_run.id,
                    ConversationRun.agent_id == Conversation.agent_id,
                    ConversationRun.status == "interrupted",
                    steer_run_actor_scope(context),
                )
            )
        ).one_or_none()
        if row is None:
            return None
        selected, source, agent_id, run_source, predecessor, legacy_interrupt_id = row
        if agent_id is None or (run_source != "resume" and predecessor is not None):
            return None
        cid, rid = conversation.id, parent_run.id
        if source == INDEPENDENT_JOB_SOURCE:
            job = await db.scalar(
                select(IndependentChatJob.id).where(
                    IndependentChatJob.child_conversation_id == cid,
                    private_scope_predicate(IndependentChatJob, context),
                    IndependentChatJob.cancel_requested_at.is_(None),
                )
            )
            if job is None or not await can_read_independent_child_run(
                db, conversation_id=cid, run_id=rid, context=context
            ):
                return None
        thread = str(cid)
        # Authorize the conversation before the native saver's separate pool is opened.
        saver_factory = await conversation_checkpointer(
            thread, context, db=db, saver=deferred_saver
        )
        try:
            saver = saver_factory()
            saved = await _selected_head(saver, thread, context.tenant_id, selected)
            if saved is None:
                return None
        except Exception as exc:  # noqa: BLE001 - an unavailable graph cannot authorize a decision
            return _unavailable(exc, rid, raise_on_unavailable)
        try:
            pending = _unanswered(saved)
        except (TypeError, ValueError, AttributeError):
            # A malformed stored write has no actionable proof. Retrying that log
            # cannot establish the identity of an interrupt.
            return None
        if pending is None:
            return None
        producer = saved.metadata.get("moldy_run_id")
        lineage = (
            await _run_lineage(db, rid, cid, agent_id, context) if run_source == "resume" else {rid}
        )
        if not lineage or (producer is not None and producer not in {str(key) for key in lineage}):
            return None
        ids = set(pending.native_kinds)
        namespaces = await _journal_namespaces(db, cid, rid, pending) if ids else {}
        if namespaces is None:
            return None
        if producer is None:
            proven = set(namespaces)
            if legacy_interrupt_id:
                proven.add(legacy_interrupt_id)
            if not ids or not ids.issubset(proven):
                return None
        for item in pending.interrupts:
            item["ns"] = namespaces.get(item["id"], [])
        if ids:
            if not await _check_approval_rows(db, pending, lineage, cid, context):
                return None
            try:
                pending.legacy_child_bindings.update(
                    await read_legacy_child_bindings(
                        saver,
                        saved,
                        root_task_ids=pending.root_task_ids,
                        tenant_id=context.tenant_id,
                        lineage=lineage,
                    )
                )
            except Exception as exc:  # noqa: BLE001 - an unreadable child cannot authorize a resume
                return _unavailable(exc, rid, raise_on_unavailable)
        return pending
