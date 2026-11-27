"""Project durable child invocation identifiers for a single private run's replay."""

from collections import Counter
from typing import NamedTuple
from uuid import UUID

from sqlalchemy import Select, and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent_runtime.ag_ui_visibility import ChildVisibility
from app.agent_runtime.lease_checkpoint_namespace import NS_SEP
from app.authz.child_task_authorization import (
    child_history_target,
    child_history_target_is_current,
)
from app.authz.org_context import OrgContext, active_memberships
from app.authz.scoping import private_scope_predicate
from app.models.conversation import Conversation
from app.models.conversation_child_task import ConversationChildTask
from app.models.conversation_run import ConversationRun
from app.models.organization import Organization, Tenant
from app.models.user import User
from app.services.child_tasks_api import allow_legacy_child_details
from app.services.conversation_run_service import readable_conversation_run_ids

_HIDDEN = ChildVisibility(False, frozenset(), frozenset())


class _Binding(NamedTuple):
    task_id: UUID
    origin: UUID
    current: UUID
    agent_id: UUID
    tool_call_id: str
    checkpoint_ns: str


async def _page_epoch(
    db: AsyncSession, conversation: Conversation, context: OrgContext, run_id: UUID
) -> int | None:
    # Recheck the authenticated actor's actual membership on both sides of a remote
    # decision. Compare the persisted source too, since it controls delegated access.
    scope = (
        active_memberships(context.user_id)
        .join(User, User.id == context.user_id)
        .join(Conversation, Conversation.org_id == Organization.id)
        .join(ConversationRun, ConversationRun.conversation_id == Conversation.id)
        .where(
            Organization.status == "active",
            Tenant.status == "active",
            User.is_active.is_(True),
            Tenant.id == context.tenant_id,
            private_scope_predicate(Conversation, context),
            Conversation.id == conversation.id,
            Conversation.source == conversation.source,
            ConversationRun.id == run_id,
            private_scope_predicate(ConversationRun, context),
            or_(
                ConversationRun.run_source == "CHAT",
                and_(
                    ConversationRun.run_source.is_(None),
                    Conversation.source == "ui",
                    ConversationRun.source.in_(("chat", "start", "edit", "regenerate", "resume")),
                ),
            ),
        )
        .with_only_columns(Organization.authz_epoch)
    )
    return await db.scalar(scope)


def _resume_relatives(
    conversation_id: UUID, context: OrgContext, run_id: UUID, *, ancestors: bool
) -> Select[tuple[UUID]]:
    """A task covers the interrupted CHAT parents between its creation and current run."""
    run = ConversationRun
    scoped = select(run.id, run.parent_run_id, run.source, run.status, run.agent_id).where(
        run.conversation_id == conversation_id,
        private_scope_predicate(run, context),
        run.run_source == "CHAT",
    )
    label = "child_origin_runs" if ancestors else "child_current_runs"
    chain = scoped.where(run.id == run_id).cte(label, recursive=True)
    if ancestors:
        step = scoped.join(chain, run.id == chain.c.parent_run_id).where(
            chain.c.source == "resume", run.status == "interrupted"
        )
    else:
        step = scoped.join(chain, run.parent_run_id == chain.c.id).where(
            run.source == "resume", chain.c.status == "interrupted"
        )
    chain = chain.union(step.where(run.agent_id == chain.c.agent_id))
    return select(chain.c.id)


async def _candidates(
    db: AsyncSession, conversation: Conversation, context: OrgContext, run_id: UUID
) -> set[_Binding]:
    task = ConversationChildTask
    query = select(
        task.id,
        task.run_id,
        task.current_run_id,
        task.child_agent_id,
        task.tool_call_id,
        task.checkpoint_ns,
    ).where(
        task.run_id.in_(_resume_relatives(conversation.id, context, run_id, ancestors=True)),
        task.current_run_id.in_(
            _resume_relatives(conversation.id, context, run_id, ancestors=False)
        ),
    )
    readable = await readable_conversation_run_ids(db, conversation, context)
    if readable is not None:
        if run_id not in readable:
            return set()
        query = query.where(task.run_id.in_(readable), task.current_run_id.in_(readable))
    return {_Binding(*row) for row in await db.execute(query)}


async def readable_child_task_visibility(
    db: AsyncSession, conversation: Conversation, context: OrgContext, run_id: UUID
) -> ChildVisibility:
    """Read allowlisted tool calls and checkpoint namespaces without claiming their run."""
    with db.no_autoflush:
        epoch = await _page_epoch(db, conversation, context, run_id)
        if epoch is None:
            return _HIDDEN
        if conversation.source == "ui" and await allow_legacy_child_details(
            db, conversation, context
        ):
            return ChildVisibility(True, frozenset(), frozenset())

        candidates = await _candidates(db, conversation, context, run_id)
        targets = {}
        for current, agent_id in {(b.current, b.agent_id) for b in candidates}:
            target = await child_history_target(
                db, context, current, agent_id, conversation_id=conversation.id, epoch=epoch
            )
            if target is not None:
                targets[current, agent_id] = target
        if not targets:
            return _HIDDEN

        latest = await _candidates(db, conversation, context, run_id)
        allowed: set[_Binding] = set()
        current_targets: dict[tuple[tuple[UUID, ...], UUID], bool] = {}
        for binding in candidates & latest:
            target = targets.get((binding.current, binding.agent_id))
            if target is None:
                continue
            runs = tuple(dict.fromkeys((binding.current, binding.origin, run_id)))
            key = (runs, binding.agent_id)
            if key not in current_targets:
                current_targets[key] = await child_history_target_is_current(
                    db, context, target, runs
                )
            if current_targets[key]:
                allowed.add(binding)
        if epoch != await _page_epoch(db, conversation, context, run_id):
            return _HIDDEN

    # A resume can rebind a task during the FGA request. Only unchanged rows can
    # contribute a public identifier. An ambiguous identifier cannot grant access.
    seen = candidates | latest
    calls = Counter(binding.tool_call_id for binding in seen)
    namespace_counts = Counter(binding.checkpoint_ns for binding in seen)
    visible_calls = frozenset(row.tool_call_id for row in allowed if calls[row.tool_call_id] == 1)
    namespaces = {tuple(row.checkpoint_ns.split(NS_SEP)) for row in allowed}
    hidden = {tuple(row.checkpoint_ns.split(NS_SEP)) for row in seen - allowed}
    # Projectors accept descendants of an allowed namespace. Suppress a prefix
    # that would otherwise give access to a hidden sibling's nested invocation.
    visible_ns = frozenset(
        ns
        for ns in namespaces
        if all(ns)
        and not any(denied[: len(ns)] == ns or ns[: len(denied)] == denied for denied in hidden)
    )
    bindings = frozenset(
        (row.tool_call_id, tuple(row.checkpoint_ns.split(NS_SEP)), str(row.task_id))
        for row in allowed
        if row.tool_call_id in visible_calls
        and namespace_counts[row.checkpoint_ns] == 1
        and tuple(row.checkpoint_ns.split(NS_SEP)) in visible_ns
    )
    return ChildVisibility(False, visible_calls, visible_ns, bindings)


async def readable_child_task_bindings(
    db: AsyncSession, conversation: Conversation, context: OrgContext, run_id: UUID
) -> tuple[bool, frozenset[str], frozenset[tuple[str, ...]]]:
    visibility = await readable_child_task_visibility(db, conversation, context, run_id)
    return visibility.allow_legacy_details, visibility.tool_call_ids, visibility.namespaces
