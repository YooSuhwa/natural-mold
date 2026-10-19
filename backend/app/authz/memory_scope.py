"""Memory roots authorize every surviving private parent before hydration or mutation.

``shared`` (F-09, 11.8) names agents the caller may run but does not own: the caller's
own records/proposals and conversations on them are admitted too. Empty (the default,
and always outside enforce) keeps the owner-only SQL unchanged.
"""

from collections.abc import Collection
from typing import Any
from uuid import UUID

from sqlalchemy import and_, or_, select
from sqlalchemy.sql.elements import ColumnElement

from app.authz.org_context import OrgContext
from app.authz.scoping import private_scope_predicate
from app.models.agent import Agent
from app.models.conversation import Conversation
from app.models.memory import MemoryProposal, MemoryRecord


def _agent_parent(
    column: Any, parent: type, context: OrgContext, shared: Collection[UUID]
) -> ColumnElement[bool]:
    owned = (
        select(Agent.id)
        .where(Agent.id == column, private_scope_predicate(Agent, context))
        .correlate(parent)
        .exists()
    )
    terms = [column.is_(None), owned]
    if shared:
        terms.append(column.in_(sorted(shared)))
    return or_(*terms)


def private_memory_source_conversation_predicate(
    context: OrgContext, shared: Collection[UUID] = ()
) -> ColumnElement[bool]:
    return and_(
        private_scope_predicate(Conversation, context),
        _agent_parent(Conversation.agent_id, Conversation, context, shared),
    )


def _source_conversation(
    column: Any, parent: type, context: OrgContext, shared: Collection[UUID]
) -> ColumnElement[bool]:
    return or_(
        column.is_(None),
        select(Conversation.id)
        .where(
            Conversation.id == column,
            private_memory_source_conversation_predicate(context, shared),
        )
        .correlate(parent)
        .exists(),
    )


def private_memory_record_predicate(
    context: OrgContext, shared: Collection[UUID] = ()
) -> ColumnElement[bool]:
    return and_(
        private_scope_predicate(MemoryRecord, context),
        _agent_parent(MemoryRecord.agent_id, MemoryRecord, context, shared),
        _source_conversation(MemoryRecord.source_conversation_id, MemoryRecord, context, shared),
    )


def private_memory_proposal_predicate(
    context: OrgContext, shared: Collection[UUID] = ()
) -> ColumnElement[bool]:
    return and_(
        private_scope_predicate(MemoryProposal, context),
        _agent_parent(MemoryProposal.agent_id, MemoryProposal, context, shared),
        _source_conversation(MemoryProposal.conversation_id, MemoryProposal, context, shared),
    )
