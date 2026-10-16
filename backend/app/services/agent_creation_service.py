"""Standard Agent creation ingress (split from ``agent_service``)."""

from __future__ import annotations

import logging
import uuid

from fastapi import HTTPException
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent_runtime.identity import make_agent_runtime_name, validate_identity_mode
from app.agent_runtime.runtime_policy import runtime_policy_to_json
from app.authz.org_context import OrgContext
from app.authz.private_actor import PrivateResourceActor, resolve_private_actor
from app.authz.scoping import private_scope_predicate, resource_candidates
from app.models.agent import Agent
from app.models.agent_subagent import AgentSubAgentLink
from app.models.mcp_tool import AgentMcpToolLink
from app.models.skill import AgentSkillLink
from app.models.template import Template
from app.models.tool import Tool
from app.schemas.agent import AgentCreate
from app.services.agent_binding_service import _build_tool_links, _validate_agent_binding_ids

logger = logging.getLogger("app.services.agent_service")


async def _install_template_skills(
    db: AsyncSession, slugs: list[str], context: OrgContext
) -> list[uuid.UUID]:
    """Install system marketplace skills referenced by a template (by slug).

    Re-uses an existing installation when the user already has one
    (``install_mode="reuse_or_update"``). Seed drift — a slug without a
    published system item, or an installation whose skill row was deleted —
    is skipped with a warning instead of failing agent creation: the agent
    is still usable and the skill can be attached manually.
    """

    from app.dependencies import CurrentUser
    from app.marketplace.install_service import install_item
    from app.marketplace.schemas import InstallMarketplaceItemIn
    from app.models.marketplace import MarketplaceItem
    from app.models.skill import Skill
    from app.models.user import User

    user_id = context.user_id
    user_row = await db.get(User, user_id)
    if user_row is None:
        return []
    current_user = CurrentUser(
        id=user_row.id,
        email=user_row.email,
        name=user_row.name,
        is_super_user=bool(user_row.is_super_user),
    )

    skill_ids: list[uuid.UUID] = []
    for slug in slugs:
        item_id = (
            await db.execute(
                select(MarketplaceItem.id)
                .where(MarketplaceItem.resource_type == "skill")
                .where(MarketplaceItem.is_system.is_(True))
                .where(MarketplaceItem.status == "published")
                .where(
                    or_(
                        MarketplaceItem.slug == slug,
                        MarketplaceItem.source_external_id == slug,
                    )
                )
                .limit(1)
            )
        ).scalar_one_or_none()
        if item_id is None:
            logger.warning(
                "template skill slug %r has no published system marketplace item; skipping",
                slug,
            )
            continue
        try:
            installation = await install_item(
                db,
                item_id=item_id,
                user=current_user,
                context=context,
                body=InstallMarketplaceItemIn(),
            )
        except HTTPException:
            logger.warning("template skill %r auto-install failed; skipping", slug, exc_info=True)
            continue
        if installation.installed_skill_id is not None:
            skill_ids.append(installation.installed_skill_id)

    if not skill_ids:
        return []
    # A re-used installation can point at a skill the user has since deleted;
    # attach only skill rows that still exist and belong to the user.
    result = await db.execute(
        select(Skill.id).where(Skill.id.in_(skill_ids), private_scope_predicate(Skill, context))
    )
    valid = {row[0] for row in result.all()}
    dangling = [str(s) for s in skill_ids if s not in valid]
    if dangling:
        logger.warning("template skill installations point at missing skills: %s", dangling)
    return [s for s in skill_ids if s in valid]


async def create_agent(db: AsyncSession, data: AgentCreate, context: OrgContext) -> Agent:
    await resolve_private_actor(
        db, PrivateResourceActor(context.user_id, context.org_id, context.tenant_id)
    )
    await _validate_agent_binding_ids(db, data, context)
    fallback_ids = data.model_fallback_ids or []

    agent_id = uuid.uuid4()
    agent = Agent(
        id=agent_id,
        user_id=context.user_id,
        org_id=context.org_id,
        tenant_id=context.tenant_id,
        runtime_name=make_agent_runtime_name(agent_id),
        identity_mode=validate_identity_mode(data.identity_mode),
        name=data.name,
        description=data.description,
        system_prompt=data.system_prompt,
        model_id=data.model_id,
        model_params=data.model_params,
        middleware_configs=[mc.model_dump() for mc in data.middleware_configs]
        if data.middleware_configs
        else None,
        runtime_policy=(
            runtime_policy_to_json(data.runtime_policy) if data.runtime_policy is not None else None
        ),
        opener_questions=data.opener_questions,
        model_fallback_list=[str(fid) for fid in fallback_ids] if fallback_ids else None,
        template_id=data.template_id,
    )

    template: Template | None = None
    if data.template_id:
        template = await db.get(Template, data.template_id)

    # Collect tools to link
    tool_ids_to_link: list[uuid.UUID] = []

    if data.tool_ids:
        tool_ids_to_link.extend(data.tool_ids)
    elif template and template.recommended_tools:
        # Auto-link from template recommended tools (by name)
        lower_names = [n.lower() for n in template.recommended_tools]
        result = await db.execute(
            resource_candidates(
                select(Tool.id).where(func.lower(Tool.name).in_(lower_names)), Tool, context
            )
        )
        tool_ids_to_link.extend(r[0] for r in result.all())

    if tool_ids_to_link:
        agent.tool_links = _build_tool_links(tool_ids_to_link)

    if data.mcp_tool_ids:
        agent.mcp_tool_links = [AgentMcpToolLink(mcp_tool_id=mid) for mid in data.mcp_tool_ids]

    if data.skill_ids:
        agent.skill_links = [AgentSkillLink(skill_id=sid) for sid in data.skill_ids]
    elif template and template.recommended_skill_slugs:
        # Auto-install system marketplace skills referenced by the template,
        # then attach them (mirrors the recommended_tools auto-link above).
        auto_skill_ids = await _install_template_skills(
            db,
            [str(s) for s in template.recommended_skill_slugs],
            context,
        )
        if auto_skill_ids:
            agent.skill_links = [AgentSkillLink(skill_id=sid) for sid in auto_skill_ids]

    if data.sub_agent_ids:
        agent.sub_agent_links = [
            AgentSubAgentLink(sub_agent_id=sid, position=idx)
            for idx, sid in enumerate(data.sub_agent_ids)
        ]

    db.add(agent)
    await db.flush()
    await db.refresh(
        agent,
        ["model", "tool_links", "mcp_tool_links", "skill_links", "sub_agent_links"],
    )
    return agent
