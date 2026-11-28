"""Owner, organization and company bindings for same-run instructions and rollovers."""

from sqlalchemy import and_, or_
from sqlalchemy.sql.elements import ColumnElement

from app.authz.conversation_run_scope import owned_run_predicate
from app.authz.org_context import OrgContext
from app.authz.scoping import private_scope_predicate
from app.models.conversation import Conversation
from app.models.conversation_run import ConversationRun
from app.models.conversation_run_input import ConversationRunInput
from app.models.conversation_run_steer import ConversationRunSteer


def _legacy_run_scope() -> ColumnElement[bool]:
    return and_(ConversationRun.org_id.is_(None), ConversationRun.tenant_id.is_(None))


def steer_run_actor_scope(context: OrgContext) -> ColumnElement[bool]:
    """Under a gated conversation, a legacy run inherits its parent's company pair."""
    return or_(
        private_scope_predicate(ConversationRun, context),
        and_(owned_run_predicate(context), _legacy_run_scope()),
    )


def steer_run_parent_scope() -> ColumnElement[bool]:
    """Cross-check all bindings before a detached worker reads instruction text."""
    return and_(
        ConversationRunSteer.conversation_id == Conversation.id,
        ConversationRunSteer.user_id == Conversation.user_id,
        ConversationRunSteer.org_id == Conversation.org_id,
        ConversationRunSteer.tenant_id == Conversation.tenant_id,
        ConversationRun.user_id == Conversation.user_id,
        or_(
            and_(
                ConversationRun.org_id == Conversation.org_id,
                ConversationRun.tenant_id == Conversation.tenant_id,
            ),
            _legacy_run_scope(),
        ),
    )


def steer_queue_input_scope() -> ColumnElement[bool]:
    return and_(
        ConversationRunInput.id == ConversationRunSteer.queued_input_id,
        ConversationRunInput.conversation_id == ConversationRunSteer.conversation_id,
        ConversationRunInput.user_id == ConversationRunSteer.user_id,
    )


def steer_queue_successor_scope() -> ColumnElement[bool]:
    return and_(
        ConversationRun.id == ConversationRunInput.run_id,
        ConversationRun.agent_id == ConversationRunInput.agent_id,
        ConversationRun.conversation_id == ConversationRunSteer.conversation_id,
        ConversationRun.user_id == ConversationRunSteer.user_id,
        ConversationRun.org_id == ConversationRunSteer.org_id,
        ConversationRun.tenant_id == ConversationRunSteer.tenant_id,
    )
