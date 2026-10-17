"""Run preparation sees an agent without its broken links (plan 10.6, 12.2; F-03).

``runnable_agent`` is the run-prep step after the agent tree is loaded: the private
capability preflight (``require_runtime_capability_tree``), then the broken-link view
(``app.authz.broken_links``) for every later load of the session, a reload when the
already-loaded tree still holds a broken link (optional links are dropped), and
``409 agent_not_ready`` when a required link of the one-hop tree is broken.
"""

from sqlalchemy.ext.asyncio import AsyncSession

from app.authz.broken_links import has_broken_links, require_runnable_links, use_runtime_link_view
from app.authz.org_context import OrgContext
from app.authz.runtime_capability_scope import require_runtime_capability_tree
from app.models.agent import Agent
from app.services import chat_service


async def runnable_agent(db: AsyncSession, agent: Agent, context: OrgContext) -> Agent:
    require_runtime_capability_tree(agent, context)
    use_runtime_link_view(db)
    if has_broken_links(agent):  # loaded before the view: reload without them
        agent = await chat_service.get_agent_with_tools(db, agent.id, context) or agent
    await require_runnable_links(db, agent.id)
    return agent
