"""Rehydrate a committed approval against the exact interrupt and its claimed inbox row."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import replace

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent_runtime.memory_paths import origin_run_path
from app.agent_runtime.runtime_config import AgentConfig
from app.approvals import service as approvals
from app.approvals.recording import decision_types
from app.authz.org_context import OrgContext
from app.exceptions import ConflictError
from app.models.approval_request import ApprovalRequest
from app.models.conversation import INDEPENDENT_JOB_SOURCE, Conversation
from app.models.conversation_run import ConversationRun
from app.services.ag_ui_pending_checkpoint import read_pending_checkpoint
from app.services.ag_ui_pending_child_authority import require_pending_child_authority
from app.services.ag_ui_resume import edited_actions_digest
from app.services.conversation_goal_worker import INPUT_KEY
from app.services.conversation_resume_decisions import decision_problem
from app.services.conversation_resume_types import ThreadInterrupt
from app.services.conversation_run_service import get_run_for_user
from app.workflows.tool_waits import adapt_resume


def _stale() -> ConflictError:
    return ConflictError("STALE_INTERRUPT", "The accepted approval no longer matches the run.")


def resume_config_transform(cfg: AgentConfig) -> Callable[[AgentConfig], AgentConfig] | None:
    """A prepared run rebuilds its config. Retain its workflow node's approval/output policy."""
    if cfg.unattended_approval != "wait":
        return None
    output_schema = cfg.output_schema

    def apply(fresh: AgentConfig) -> AgentConfig:
        return replace(fresh, unattended_approval="wait", output_schema=output_schema)

    return apply


def _confirmed_decision(
    row: ApprovalRequest,
    *,
    run: ConversationRun,
    parent: ConversationRun,
    interrupt: ThreadInterrupt,
    answer: object,
    context: OrgContext,
) -> bool:
    decisions = decision_types(answer)
    if not decisions or not isinstance(answer, Mapping):
        return False
    all_rejected = all(kind == "reject" for kind in decisions)
    if row.channel == "expiry":
        valid_outcome = (
            all_rejected and row.status == "expired" and row.reason == "approval_expired"
        )
    else:
        status = "rejected" if all_rejected else "edited" if "edit" in decisions else "approved"
        valid_outcome = (
            row.channel == "chat"
            and row.status == status
            and approvals.aware(row.expires_at) > approvals.now()
        )
    return (
        valid_outcome
        and row.kind == "tool"
        and row.resumed_run_id == run.id
        and row.conversation_id == run.conversation_id
        and row.agent_id == run.agent_id
        and (row.tenant_id, row.org_id) == (context.tenant_id, context.org_id)
        and row.subject_user_id == row.decider_user_id == row.decided_by == context.user_id
        and row.decisions == decisions
        and row.decision_no == 1
        and (not row.namespace or row.namespace == interrupt["ns"])
        and row.edited_args_digest == edited_actions_digest(answer)
        and (row.policy_hash, row.policy_version)
        == (parent.runtime_policy_hash, parent.runtime_policy_version)
        and isinstance(interrupt["value"], Mapping)
        and row.args_digest == approvals.args_digest(interrupt["value"])
    )


async def prepare_resume_delivery(
    db: AsyncSession,
    *,
    run: ConversationRun,
    conversation: Conversation,
    context: OrgContext,
    cfg: AgentConfig,
    checkpoint_id: str,
    payload: object,
) -> object:
    """Return the already authenticated native Command(resume=...) argument without re-claiming."""
    scope = (context.user_id, context.org_id, context.tenant_id)
    if (
        run.source != "resume"
        or run.parent_run_id is None
        or run.status != "queued"
        or not run.is_active
        or run.cancel_requested_at is not None
        or run.conversation_id != conversation.id
        or run.agent_id != conversation.agent_id
        or (run.user_id, run.org_id, run.tenant_id) != scope
        or (conversation.user_id, conversation.org_id, conversation.tenant_id) != scope
        or cfg.independent_job != (conversation.source == INDEPENDENT_JOB_SOURCE)
        or cfg.checkpoint_id != checkpoint_id
        or cfg.thread_id != str(conversation.id)
        or not isinstance(payload, dict)
    ):
        raise _stale()
    ids, answers, claims = (
        payload.get("interruptIds"),
        payload.get("response"),
        payload.get("approvalRequestIds"),
    )
    inherited = payload.get("inheritedApprovalRequestIds", {})
    if (
        not isinstance(ids, list)
        or not ids
        or not all(isinstance(i, str) and i for i in ids)
        or len(set(ids)) != len(ids)
        or not isinstance(answers, dict)
        or set(answers) != set(ids)
        or not isinstance(claims, dict)
        or not set(claims).issubset(answers)
        or not isinstance(inherited, dict)
        or not set(inherited).issubset(answers)
        or not all(
            isinstance(rows, list) and all(isinstance(v, str) for v in rows)
            for rows in inherited.values()
        )
    ):
        raise _stale()

    parent = await get_run_for_user(
        db, conversation_id=conversation.id, run_id=run.parent_run_id, context=context
    )
    if (
        parent is None
        or parent.status != "interrupted"
        or parent.agent_id != run.agent_id
        or (parent.org_id, parent.tenant_id)
        not in {(None, None), (context.org_id, context.tenant_id)}
        or (parent.metadata_json or {}).get(INPUT_KEY) != (run.metadata_json or {}).get(INPUT_KEY)
    ):
        raise _stale()
    pending = await read_pending_checkpoint(
        db, conversation=conversation, context=context, parent_run=parent, raise_on_unavailable=True
    )
    if pending is None or pending.checkpoint_id != checkpoint_id:
        raise _stale()
    interrupts = {item["id"]: item for item in pending.interrupts}
    if set(interrupts) != set(ids) or len(interrupts) != len(pending.interrupts):
        raise _stale()
    for ident, interrupt in interrupts.items():
        if decision_problem(answers[ident], interrupt["value"]) is not None:
            raise _stale()
    await require_pending_child_authority(
        db, conversation=conversation, context=context, parent_run=parent, pending=pending
    )
    approved_runs = pending.approval_run_ids
    owners = {parent.id}.union(owner for chain in approved_runs.values() for owner in chain)
    records = await db.execute(
        select(ApprovalRequest, ConversationRun)
        .join(ConversationRun, ConversationRun.id == ApprovalRequest.run_id)
        .where(ApprovalRequest.run_id.in_(owners), ApprovalRequest.interrupt_id.in_(ids))
        .execution_options(populate_existing=True)
    )
    local_claims: dict[str, str] = {}
    inherited_claims: dict[str, list[str]] = {}
    for row, owner in records:
        ident = row.interrupt_id
        if owner.id == parent.id:
            local_claims[ident] = str(row.id)
        elif owner.id in approved_runs.get(ident, ()):
            inherited_claims.setdefault(ident, []).append(str(row.id))
        else:
            raise _stale()
        if not _confirmed_decision(
            row,
            run=run,
            parent=owner,
            interrupt=interrupts[ident],
            answer=answers[ident],
            context=context,
        ):
            raise _stale()
    if local_claims != claims or (
        {key: sorted(vals) for key, vals in inherited_claims.items()}
        != {key: sorted(vals) for key, vals in inherited.items()}
    ):
        raise _stale()
    for ident in interrupts:
        if ident not in local_claims and ident not in inherited_claims:
            expires = pending.expires_at.get(ident)
            if expires is not None and approvals.aware(expires) <= approvals.now():
                raise _stale()

    cfg.run_path = await origin_run_path(db, parent)
    await adapt_resume(db, parent, cfg)
    return answers
