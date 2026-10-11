"""API control projections require exact private roots throughout their children."""

from sqlalchemy import and_, or_, select
from sqlalchemy.sql.elements import ColumnElement

from app.authz.org_context import OrgContext
from app.authz.scoping import private_scope_predicate
from app.models.agent import Agent
from app.models.agent_api import AgentApiKey, AgentApiKeyDeployment, AgentApiThread, AgentDeployment
from app.models.conversation import Conversation


def private_deployment_predicate(context: OrgContext) -> ColumnElement[bool]:
    return and_(
        private_scope_predicate(AgentDeployment, context),
        select(Agent.id)
        .where(
            Agent.id == AgentDeployment.agent_id,
            private_scope_predicate(Agent, context),
        )
        .exists(),
    )


def private_api_key_predicate(context: OrgContext) -> ColumnElement[bool]:
    invalid_child = (
        select(AgentApiKeyDeployment.api_key_id)
        .outerjoin(AgentDeployment, AgentDeployment.id == AgentApiKeyDeployment.deployment_id)
        .where(
            AgentApiKeyDeployment.api_key_id == AgentApiKey.id,
            or_(AgentDeployment.id.is_(None), ~private_deployment_predicate(context)),
        )
        .exists()
    )
    return and_(private_scope_predicate(AgentApiKey, context), ~invalid_child)


def private_api_thread_parents_predicate(context: OrgContext) -> ColumnElement[bool]:
    """Bound surviving Thread parents; the caller also scopes the Thread's direct owner."""
    agent = (
        select(Agent.id)
        .where(Agent.id == Conversation.agent_id, private_scope_predicate(Agent, context))
        .correlate(Conversation)
        .exists()
    )
    conversation = (
        select(Conversation.id)
        .where(
            Conversation.id == AgentApiThread.conversation_id,
            private_scope_predicate(Conversation, context),
            or_(Conversation.agent_id.is_(None), agent),
        )
        .correlate(AgentApiThread)
        .exists()
    )
    deployment = (
        select(AgentDeployment.id)
        .where(
            AgentDeployment.id == AgentApiThread.deployment_id,
            private_deployment_predicate(context),
        )
        .correlate(AgentApiThread)
        .exists()
    )
    return and_(deployment, conversation)
