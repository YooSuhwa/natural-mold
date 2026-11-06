"""Ownership predicates of the approval chapter (feature plan v2 순번 13, chapter 17).

Kept in the authorization package like every other ownership predicate: the schedule
limit per person, the agents one person owns (an emergency stop of that person) and the
runs of the run status tab (runs I started and runs of agents I own), each inside one
company.
"""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import ColumnElement, and_, or_

from app.authz.org_context import OrgContext
from app.models.agent import Agent
from app.models.agent_trigger import AgentTrigger
from app.models.conversation_run import ConversationRun


def schedules_of_person(context: OrgContext) -> ColumnElement[bool]:
    """The caller's schedules in the current company (17.3 limit per person)."""
    return and_(
        AgentTrigger.user_id == context.user_id, AgentTrigger.tenant_id == context.tenant_id
    )


def agents_of_person(tenant_id: UUID, user_id: UUID) -> ColumnElement[bool]:
    """Every agent one person owns in a company (17.4 stop of a person)."""
    return and_(Agent.user_id == user_id, Agent.tenant_id == tenant_id)


def runs_started_or_owned(context: OrgContext) -> ColumnElement[bool]:
    """Runs I started or of agents I own, in the current company (17.2 run status).

    The query must outer-join ``Agent`` on ``ConversationRun.agent_id``.
    """
    return and_(
        ConversationRun.tenant_id == context.tenant_id,
        or_(ConversationRun.user_id == context.user_id, Agent.user_id == context.user_id),
    )


__all__ = ["agents_of_person", "runs_started_or_owned", "schedules_of_person"]
