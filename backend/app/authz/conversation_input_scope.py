"""Execution queue children inherit their Conversation's owner and Agent binding."""

from sqlalchemy import and_

from app.authz.scoping import PrivateParent
from app.models.conversation import Conversation
from app.models.conversation_run_input import ConversationRunInput


def conversation_input_parent() -> PrivateParent:
    """Bind execution input to the same owner and Agent on its scoped parent.

    Retained conversation history has a separate, Agent-optional read boundary.
    """
    return PrivateParent(
        Conversation,
        and_(
            Conversation.id == ConversationRunInput.conversation_id,
            Conversation.user_id == ConversationRunInput.user_id,
            Conversation.agent_id == ConversationRunInput.agent_id,
        ),
    )
