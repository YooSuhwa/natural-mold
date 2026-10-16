"""Chat resource-context references through ``AuthzService`` (plan 11.7, 9.3; D-08).

Files, artifacts and conversations are the caller's private data and keep their
private loaders. A referenced skill is a shared resource: the caller needs
``can_view`` on it, and it must be linked to the conversation's agent. Resolution
also runs from queue workers, so the process service comes from ``runtime_authz()``.

legacy: the existing owner-scoped row is the decision, with no extra query.
shadow: same answer plus a sample. enforce: OpenFGA decides over the in-organization
row linked to the agent; any denial is the uniform "not found" of the caller.
"""

from typing import Final
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.authz import runtime as authz_runtime
from app.authz.candidates import candidate_predicate
from app.authz.org_context import OrgContext
from app.authz.principal import Principal
from app.authz.service_types import Verdict
from app.exceptions import AppError
from app.models.skill import AgentSkillLink, Skill

SKILL_PERMISSION: Final = "can_view"
_DENIED: Final = frozenset({403, 404})


def _resource_org(skill: Skill | None, ctx: OrgContext) -> UUID | None:
    if skill is None:
        return None
    return ctx.org_id if skill.is_system and skill.org_id is None else skill.org_id


async def authorize_context_skill(
    db: AsyncSession, ctx: OrgContext, agent_id: UUID, skill_id: UUID, *, legacy: Skill | None
) -> Skill | None:
    """The linked skill when the caller may view it; ``legacy`` is the owner-scoped row."""
    authz = authz_runtime.runtime_authz()
    row = legacy
    if row is None and authz.mode != "legacy":
        row = await db.scalar(
            select(Skill)
            .join(AgentSkillLink, AgentSkillLink.skill_id == Skill.id)
            .where(
                Skill.id == skill_id,
                AgentSkillLink.agent_id == agent_id,
                candidate_predicate(Skill, "skill", ctx, platform=True),
            )
            .execution_options(autoflush=False, populate_existing=True)
        )
    try:
        await authz.require(
            Principal.for_user(ctx),
            SKILL_PERMISSION,
            f"skill:{skill_id}",
            min_visible="can_use",
            resource_org_id=_resource_org(row, ctx),
            legacy=Verdict.ALLOW if legacy is not None else Verdict.NOT_FOUND,
        )
    except AppError as denied:
        if denied.status in _DENIED:
            return None
        raise
    return row
