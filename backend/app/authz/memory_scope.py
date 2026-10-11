"""Memory roots authorize every surviving private parent before hydration or mutation."""

from sqlalchemy import and_, or_, select
from sqlalchemy.sql.elements import ColumnElement

from app.authz.org_context import OrgContext
from app.authz.scoping import private_scope_predicate
from app.models.agent import Agent
from app.models.conversation import Conversation
from app.models.memory import MemoryProposal, MemoryRecord


def private_memory_source_conversation_predicate(context: OrgContext) -> ColumnElement[bool]:
    agent = (
        select(Agent.id)
        .where(Agent.id == Conversation.agent_id, private_scope_predicate(Agent, context))
        .correlate(Conversation)
        .exists()
    )
    return and_(
        private_scope_predicate(Conversation, context),
        or_(Conversation.agent_id.is_(None), agent),
    )


def private_memory_record_predicate(context: OrgContext) -> ColumnElement[bool]:
    return and_(
        private_scope_predicate(MemoryRecord, context),
        or_(
            MemoryRecord.agent_id.is_(None),
            select(Agent.id)
            .where(Agent.id == MemoryRecord.agent_id, private_scope_predicate(Agent, context))
            .correlate(MemoryRecord)
            .exists(),
        ),
        or_(
            MemoryRecord.source_conversation_id.is_(None),
            select(Conversation.id)
            .where(
                Conversation.id == MemoryRecord.source_conversation_id,
                private_memory_source_conversation_predicate(context),
            )
            .correlate(MemoryRecord)
            .exists(),
        ),
    )


def private_memory_proposal_predicate(context: OrgContext) -> ColumnElement[bool]:
    return and_(
        private_scope_predicate(MemoryProposal, context),
        or_(
            MemoryProposal.agent_id.is_(None),
            select(Agent.id)
            .where(Agent.id == MemoryProposal.agent_id, private_scope_predicate(Agent, context))
            .correlate(MemoryProposal)
            .exists(),
        ),
        or_(
            MemoryProposal.conversation_id.is_(None),
            select(Conversation.id)
            .where(
                Conversation.id == MemoryProposal.conversation_id,
                private_memory_source_conversation_predicate(context),
            )
            .correlate(MemoryProposal)
            .exists(),
        ),
    )
