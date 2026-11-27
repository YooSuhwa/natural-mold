"""Read confirmed tool decisions for a branch without returning approval arguments."""

from __future__ import annotations

import re
from collections import defaultdict
from collections.abc import Collection
from dataclasses import dataclass
from itertools import batched
from typing import Literal
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import load_only, raiseload

from app.approvals import service as approvals
from app.approvals.service import _status_for
from app.authz.conversation_scope import require_private_conversation
from app.authz.org_context import OrgContext
from app.authz.scoping import private_scope_predicate
from app.models.approval_request import ApprovalRequest
from app.models.conversation import INDEPENDENT_JOB_SOURCE, Conversation
from app.models.conversation_run import ConversationRun
from app.services.ag_ui_read_access import load_child_visibility
from app.services.ag_ui_read_pending import constrain_legacy_child_visibility
from app.services.independent_job_runs import readable_independent_child_run_ids

type Decision = Literal["approve", "reject", "edit"]


@dataclass(frozen=True, slots=True)
class ConsumedApprovalAction:
    index: int
    tool_name: str
    decision: Decision
    display_args_digest: str | None = None


@dataclass(frozen=True, slots=True)
class ConsumedApprovalDecision:
    """Internal evidence for matching an approval to its public tool issuance."""

    parent_run_id: UUID
    resumed_run_id: UUID
    source_run_ids: tuple[UUID, ...]
    interrupt_id: str
    namespace: tuple[str, ...]
    args_digest: str
    actions: tuple[ConsumedApprovalAction, ...]


def _actions(row: ApprovalRequest) -> tuple[ConsumedApprovalAction, ...]:
    choices = row.decisions
    if (
        not isinstance(choices, list)
        or not choices
        or not isinstance(row.actions, list)
        or len(choices) != len(row.actions)
        or not row.decided_at
        or row.decision_no != 1
        or row.channel not in ("chat", "inbox")
        or row.source not in ("chat", "subagent")
        or row.kind != "tool"
        or row.decided_by != row.decider_user_id
        or row.status != _status_for(choices)
        or not re.fullmatch(r"[0-9a-f]{64}", row.args_digest or "")
    ):
        return ()
    decided = approvals.aware(row.decided_at)
    if (
        not (approvals.aware(row.created_at) <= decided < approvals.aware(row.expires_at))
        or decided > approvals.now()
    ):
        return ()
    items: list[ConsumedApprovalAction] = []
    for index, (choice, action) in enumerate(zip(choices, row.actions, strict=True)):
        if choice not in ("approve", "reject", "edit") or not isinstance(action, dict):
            return ()
        name = action.get("name")
        permitted = action.get("allowed_decisions")
        if (
            not isinstance(name, str)
            or not name
            or not isinstance(permitted, list)
            or choice not in permitted
        ):
            return ()
        args = action.get("args")
        items.append(
            ConsumedApprovalAction(
                index, name, choice, approvals.digest(args) if isinstance(args, dict) else None
            )
        )
    return tuple(items)


def _paused_lineage(child: ConversationRun, by_id: dict[UUID, ConversationRun]) -> tuple[UUID, ...]:
    if child.source != "resume" or child.status not in ("running", "interrupted", "completed"):
        return ()
    result: list[UUID] = []
    seen = {child.id}
    parent_id = child.parent_run_id
    while parent_id is not None and parent_id not in seen:
        parent = by_id.get(parent_id)
        if parent is None or parent.status != "interrupted" or parent.agent_id != child.agent_id:
            return ()
        result.append(parent.id)
        seen.add(parent.id)
        if parent.source != "resume":
            return tuple(reversed(result)) if parent.parent_run_id is None else ()
        parent_id = parent.parent_run_id
    return ()


async def load_consumed_approval_decisions(
    db: AsyncSession,
    *,
    conversation: Conversation,
    context: OrgContext,
    branch_run_ids: Collection[UUID],
) -> list[ConsumedApprovalDecision]:
    """Read accepted decisions using only physical runs already proved on the current branch."""
    if not branch_run_ids:
        return []
    with db.no_autoflush:
        conversation = await require_private_conversation(db, conversation.id, context)
        allowed = set(branch_run_ids)
        if conversation.source == INDEPENDENT_JOB_SOURCE:
            allowed &= await readable_independent_child_run_ids(
                db, conversation_id=conversation.id, run_ids=allowed, context=context
            )
        attributions = {(None, None), ("CHAT", "user")}
        if conversation.source == INDEPENDENT_JOB_SOURCE:
            attributions.add(("SUBAGENT", "subagent"))
        by_id: dict[UUID, ConversationRun] = {}
        for group in batched(allowed, 256):
            rows = await db.scalars(
                select(ConversationRun)
                .where(
                    ConversationRun.id.in_(group),
                    ConversationRun.conversation_id == conversation.id,
                    private_scope_predicate(ConversationRun, context),
                )
                .options(raiseload(ConversationRun.metrics))
            )
            by_id.update(
                (row.id, row) for row in rows if (row.run_source, row.invoker_type) in attributions
            )
        paths = {rid: path for rid, run in by_id.items() if (path := _paused_lineage(run, by_id))}
        if not paths:
            return []

        request = ApprovalRequest
        stmt = (
            select(request)
            .where(
                request.conversation_id == conversation.id,
                request.tenant_id == context.tenant_id,
                request.org_id == context.org_id,
                request.subject_user_id == context.user_id,
                request.decider_user_id == context.user_id,
            )
            .options(
                load_only(
                    request.id,
                    request.agent_id,
                    request.created_at,
                    request.run_id,
                    request.resumed_run_id,
                    request.interrupt_id,
                    request.source,
                    request.kind,
                    request.namespace,
                    request.status,
                    request.channel,
                    request.decision_no,
                    request.decisions,
                    request.actions,
                    request.args_digest,
                    request.decider_user_id,
                    request.decided_by,
                    request.decided_at,
                    request.expires_at,
                )
            )
        )
        groups: dict[tuple[UUID, str], list[ApprovalRequest]] = defaultdict(list)
        for resumes in batched(paths, 256):
            for originals in batched(by_id, 256):
                proofs = await db.scalars(
                    stmt.where(request.resumed_run_id.in_(resumes), request.run_id.in_(originals))
                )
                for row in proofs:
                    if row.resumed_run_id and row.interrupt_id:
                        groups[row.resumed_run_id, row.interrupt_id].append(row)
        result = []
        visibility_by_run: dict[UUID, frozenset[tuple[str, ...]]] = {}
        for (rid, iid), receipts in groups.items():
            path = paths[rid]
            facts = []
            namespaces: set[tuple[str, ...]] = set()
            origins: set[UUID] = set()
            for row in receipts:
                if (
                    row.run_id not in path
                    or row.agent_id != by_id[rid].agent_id
                    or not isinstance(row.namespace, list)
                ):
                    break
                if any(not isinstance(part, str) or not part for part in row.namespace):
                    break
                actions = _actions(row)
                if not actions or row.run_id is None:
                    break
                ns = tuple(row.namespace)
                if ns:
                    namespaces.add(ns)
                origins.add(row.run_id)
                facts.append((row.args_digest, actions))
            else:
                if len(set(facts)) != 1 or len(namespaces) > 1:
                    continue
                ns = next(iter(namespaces), ())
                if ns:
                    for source in origins:
                        if source not in visibility_by_run:
                            visibility = await load_child_visibility(
                                db, conversation, context, source
                            )
                            visibility = await constrain_legacy_child_visibility(
                                db, conversation, context, by_id[source], visibility
                            )
                            visibility_by_run[source] = visibility.namespaces
                    if not all(
                        any(ns[: len(known)] == known for known in visibility_by_run[source])
                        for source in origins
                    ):
                        continue
                digest, actions = facts[0]
                result.append(
                    ConsumedApprovalDecision(
                        path[-1],
                        rid,
                        tuple(key for key in path if key in origins),
                        iid,
                        ns,
                        digest,
                        actions,
                    )
                )
        return sorted(
            result,
            key=lambda record: (
                by_id[record.resumed_run_id].created_at,
                record.resumed_run_id,
                record.interrupt_id,
            ),
        )
