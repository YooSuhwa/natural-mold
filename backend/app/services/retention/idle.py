"""When a conversation counts as idle for ``conversation_days`` (plan 10.7 "대화 보존").

``last_activity_at`` is touched only by the interactive chat paths, so schedule
(trigger) and Agent API turns would leave it behind. Instead of teaching every writer
to touch it, idleness requires every activity trace to be older than the cutoff: the
conversation row itself (``last_activity_at`` and ``updated_at``), its conversation
runs, trigger runs, Agent API runs and Agent API threads.

Known limit: a conversation parked on a human approval (HITL) has no active run, so it
is deleted once all of the above are older than the period.
"""

from datetime import datetime

from sqlalchemy import ColumnElement, and_, exists, func, or_, select

from app.models.agent_api import AgentApiRun, AgentApiThread
from app.models.agent_trigger_run import AgentTriggerRun
from app.models.conversation import Conversation
from app.models.conversation_run import ConversationRun


def idle_before(cutoff: datetime) -> ColumnElement[bool]:
    """``cutoff`` is an aware UTC instant; naive columns compare to its naive UTC value."""
    naive = cutoff.replace(tzinfo=None)
    recent_run = exists(
        select(ConversationRun.id).where(
            ConversationRun.conversation_id == Conversation.id,
            or_(ConversationRun.created_at >= naive, ConversationRun.updated_at >= naive),
        )
    )
    recent_trigger = exists(
        select(AgentTriggerRun.id).where(
            AgentTriggerRun.conversation_id == Conversation.id,
            func.coalesce(AgentTriggerRun.finished_at, AgentTriggerRun.created_at) >= naive,
        )
    )
    recent_api_run = exists(
        select(AgentApiRun.id).where(
            AgentApiRun.conversation_id == Conversation.id,
            func.coalesce(AgentApiRun.finished_at, AgentApiRun.created_at) >= naive,
        )
    )
    recent_thread = exists(
        select(AgentApiThread.id).where(
            AgentApiThread.conversation_id == Conversation.id,
            AgentApiThread.updated_at >= naive,
        )
    )
    return and_(
        Conversation.last_activity_at < cutoff,
        Conversation.updated_at < naive,
        ~recent_run,
        ~recent_trigger,
        ~recent_api_run,
        ~recent_thread,
    )
