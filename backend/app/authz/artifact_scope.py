"""An artifact's root and conversation must agree on private organization scope."""

from uuid import UUID

from sqlalchemy import String, and_, cast, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.elements import ColumnElement

from app.authz.conversation_scope import load_private_conversation, resolve_conversation_actor
from app.authz.independent_job_history import readable_job_conversations
from app.authz.org_context import OrgContext
from app.authz.published_conversation import PublishedConversationContext
from app.authz.scoping import private_scope_predicate
from app.error_codes import conversation_not_found
from app.models.conversation import INDEPENDENT_JOB_SOURCE, Conversation
from app.models.conversation_artifact import ArtifactVersion, ConversationArtifact
from app.services.independent_job_runs import job_run_lineage


def private_artifact_predicate(context: OrgContext) -> ColumnElement[bool]:
    """Private scope for callers that do not authorize independent job results."""
    ordinary = (
        select(Conversation.id)
        .where(
            Conversation.id == ConversationArtifact.conversation_id,
            private_scope_predicate(Conversation, context),
            Conversation.source != INDEPENDENT_JOB_SOURCE,
        )
        .correlate(ConversationArtifact)
        .exists()
    )
    return and_(private_scope_predicate(ConversationArtifact, context), ordinary)


async def readable_artifact_predicate(
    db: AsyncSession, context: OrgContext, *filters: ColumnElement[bool]
) -> ColumnElement[bool]:
    """Authorize private files before hydration, pagination or aggregation.

    Only independent conversations with matching artifacts require a fresh
    delegation check. The explicit source check also hides orphan children
    whose job or parent was already deleted.
    """
    root_scope = private_scope_predicate(ConversationArtifact, context)
    conversation_scope = private_scope_predicate(Conversation, context)
    pinned_version = (
        select(ArtifactVersion.id)
        .where(
            ArtifactVersion.id == ConversationArtifact.current_version_id,
            ArtifactVersion.artifact_id == ConversationArtifact.id,
        )
        .correlate(ConversationArtifact)
    )
    valid_version = or_(ConversationArtifact.current_version_id.is_(None), pinned_version.exists())
    if isinstance(context, PublishedConversationContext):
        # The redeemed share's visible-message projection gates each result.
        # Its explicit publication grant is bound to this one conversation.
        return and_(
            root_scope,
            select(Conversation.id)
            .where(Conversation.id == context.published_conversation_id, conversation_scope)
            .exists(),
            ConversationArtifact.conversation_id == context.published_conversation_id,
            valid_version,
        )
    ordinary = private_artifact_predicate(context)
    child_ids = await db.scalars(
        select(Conversation.id)
        .join(ConversationArtifact, ConversationArtifact.conversation_id == Conversation.id)
        .where(
            conversation_scope, root_scope, Conversation.source == INDEPENDENT_JOB_SOURCE, *filters
        )
        .distinct()
        .execution_options(autoflush=False)
    )
    # This is the same batch live-grant check used by the child chat read
    # boundary. The recursive lineage below also verifies its initial run.
    jobs = await readable_job_conversations(db, context, conversation_ids=child_ids.all())
    if not jobs:
        return ordinary

    chain = job_run_lineage(context, list(jobs))
    # Managed artifact recorders use str(run.id). SQL UUIDs are stored
    # without hyphens on SQLite and with hyphens on PostgreSQL.
    turn = ConversationArtifact.assistant_msg_id
    authorized_turn = (
        select(chain.c.run_id)
        .where(
            chain.c.conversation_id == ConversationArtifact.conversation_id,
            chain.c.agent_id == ConversationArtifact.agent_id,
            turn.like("________-____-____-____-____________"),
            func.replace(turn, "-", "") == func.replace(cast(chain.c.run_id, String), "-", ""),
        )
        .exists()
    )
    independent = and_(authorized_turn, valid_version)
    return or_(ordinary, and_(root_scope, independent))


async def validated_artifact_conversation(
    db: AsyncSession, conversation_id: UUID, context: OrgContext
) -> Conversation:
    conversation = await load_private_conversation(db, conversation_id, context)
    if conversation is None:
        raise conversation_not_found()
    await resolve_conversation_actor(db, conversation_id, context.user_id)
    return conversation
