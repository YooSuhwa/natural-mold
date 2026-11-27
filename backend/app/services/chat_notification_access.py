"""Resolve stored chat transitions against today's recipient, run and result version."""

from __future__ import annotations

from collections.abc import Collection, Mapping
from uuid import UUID

from sqlalchemy import and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.authz import denials
from app.authz.notifications import RETENTION, now
from app.authz.org_context import OrgContext
from app.authz.private_actor import require_private_actor
from app.exceptions import AppError
from app.models.chat_notification_outbox import ChatNotificationOutbox
from app.models.conversation import Conversation
from app.models.conversation_artifact import ArtifactVersion, ConversationArtifact
from app.models.conversation_goal import ConversationGoal, GoalTransitionIntent
from app.models.conversation_run import ConversationRun
from app.models.notification import Notification
from app.schemas.notification import ChatNotificationDestination
from app.services.artifacts.errors import ArtifactNotFoundError

WORK_TARGET = "chat_work_transition"


def _references_checked_version(
    payload: Mapping[str, object],
    artifact_id: UUID | None,
    version_id: UUID | None,
    sha256: str | None,
) -> bool:
    evidence = payload.get("evidence")
    if not isinstance(evidence, dict) or evidence.get("all_passed") is not True:
        return False
    checks = evidence.get("checks")
    if not isinstance(checks, list):
        return False
    return any(
        isinstance(check, dict)
        and check.get("passed") is True
        and artifact_id is not None
        and check.get("artifact_id") == str(artifact_id)
        and check.get("version_id") == str(version_id)
        and check.get("sha256") == sha256
        for check in checks
    )


async def _authorized_destinations(
    db: AsyncSession, ctx: OrgContext, ids: Collection[UUID], *, published: bool
) -> dict[UUID, ChatNotificationDestination]:
    if not ids:
        return {}
    # The delivery worker and both GET routes must use the same live membership check.
    try:
        await require_private_actor(db, ctx)
    except AppError as exc:
        if exc.status not in {403, 404, 409}:
            raise
        return {}

    ref = ChatNotificationOutbox
    goal = ConversationGoal
    intent = GoalTransitionIntent
    version = ArtifactVersion
    artifact = ConversationArtifact
    query = (
        select(
            ref, Conversation.agent_id, version.original_filename, version.sha256, intent.payload
        )
        .join(Conversation, Conversation.id == ref.conversation_id)
        .join(ConversationRun, ConversationRun.id == ref.run_id)
        .outerjoin(goal, goal.id == ref.goal_id)
        .outerjoin(intent, intent.id == ref.source_id)
        .outerjoin(artifact, artifact.id == ref.artifact_id)
        .outerjoin(version, version.id == ref.version_id)
        .where(
            ref.id.in_(ids),
            ref.user_id == ctx.user_id,
            ref.org_id == ctx.org_id,
            ref.tenant_id == ctx.tenant_id,
            Conversation.user_id == ref.user_id,
            Conversation.org_id == ref.org_id,
            Conversation.tenant_id == ref.tenant_id,
            ConversationRun.conversation_id == ref.conversation_id,
            ConversationRun.user_id == ref.user_id,
            ConversationRun.org_id == ref.org_id,
            ConversationRun.tenant_id == ref.tenant_id,
            or_(
                and_(ref.artifact_id.is_(None), ref.version_id.is_(None)),
                and_(
                    artifact.conversation_id == ref.conversation_id,
                    artifact.user_id == ref.user_id,
                    artifact.org_id == ref.org_id,
                    artifact.tenant_id == ref.tenant_id,
                    artifact.status == "ready",
                    version.artifact_id == artifact.id,
                ),
            ),
            or_(
                and_(
                    goal.conversation_id == ref.conversation_id,
                    goal.user_id == ref.user_id,
                    goal.org_id == ref.org_id,
                    goal.tenant_id == ref.tenant_id,
                    goal.version == ref.goal_version,
                    goal.revision == ref.goal_revision,
                    intent.goal_id == ref.goal_id,
                    intent.goal_version == ref.goal_version,
                    intent.goal_revision == ref.goal_revision,
                    intent.run_id == ref.run_id,
                    intent.kind == goal.status,
                    or_(
                        and_(ref.kind == "chat_goal.completed", goal.status == "completed"),
                        and_(ref.kind == "chat_goal.blocked", goal.status == "blocked"),
                    ),
                ),
                and_(
                    ref.kind == "chat_task.completed",
                    Conversation.source == "independent_job",
                    ConversationRun.status == "completed",
                ),
            ),
        )
    )
    if published:
        query = query.join(Notification, Notification.id == ref.notification_id).where(
            Notification.user_id == ref.user_id,
            Notification.org_id == ref.org_id,
            Notification.tenant_id == ref.tenant_id,
            Notification.target_type == WORK_TARGET,
            Notification.target_id == ref.id,
            Notification.kind == ref.kind,
            Notification.created_at >= now() - RETENTION,
        )
    found = {}
    for row, agent, filename, digest, payload in (await db.execute(query)).tuples():
        if row.kind == "chat_goal.completed" and not _references_checked_version(
            payload or {}, row.artifact_id, row.version_id, digest
        ):
            continue
        found[row.id] = ChatNotificationDestination(
            conversation_id=row.conversation_id,
            agent_id=agent,
            run_id=row.run_id,
            goal_id=row.goal_id,
            job_id=row.source_id if row.kind == "chat_task.completed" else None,
            artifact_id=row.artifact_id,
            version_id=row.version_id,
            filename=filename,
        )
    return found


async def resolve_work_destination(
    db: AsyncSession, ref: ChatNotificationOutbox
) -> ChatNotificationDestination | None:
    ctx = OrgContext(org_id=ref.org_id, tenant_id=ref.tenant_id, user_id=ref.user_id)
    destinations = await _authorized_destinations(db, ctx, [ref.id], published=False)
    return destinations.get(ref.id)


async def accessible_work_targets(
    db: AsyncSession, ctx: OrgContext, ids: Collection[UUID]
) -> set[str]:
    found = await _authorized_destinations(db, ctx, ids, published=True)
    return {f"{WORK_TARGET}:{key}" for key in found}


async def open_chat_notification(
    db: AsyncSession, ctx: OrgContext, notification_id: UUID
) -> ChatNotificationDestination:
    target = await db.scalar(
        select(Notification.target_id)
        .join(ChatNotificationOutbox, ChatNotificationOutbox.notification_id == Notification.id)
        .where(
            ChatNotificationOutbox.id == Notification.target_id,
            Notification.id == notification_id,
            Notification.user_id == ctx.user_id,
            Notification.org_id == ctx.org_id,
            Notification.tenant_id == ctx.tenant_id,
            Notification.target_type == WORK_TARGET,
        )
    )
    if target is not None:
        found = await _authorized_destinations(db, ctx, [target], published=True)
        if target in found:
            return found[target]
    raise denials.not_found()


async def get_chat_notification_result(
    db: AsyncSession, ctx: OrgContext, notification_id: UUID
) -> tuple[bytes, str]:
    from app.services.conversation_goal_evidence import read_goal_evidence_version

    destination = await open_chat_notification(db, ctx, notification_id)
    if destination.version_id is None or destination.artifact_id is None:
        raise denials.not_found()
    try:
        version, content = await read_goal_evidence_version(
            db,
            context=ctx,
            conversation_id=destination.conversation_id,
            artifact_id=destination.artifact_id,
            version_id=destination.version_id,
        )
    except ArtifactNotFoundError as exc:
        raise denials.not_found() from exc
    return content, version.original_filename
