"""Skill-family route decisions through ``AuthzService`` (plan 9.4, D-03).

The legacy answer is always the existing private lookup (``skill_service.get_skill``
/ ``list_skills``), so ``AUTHZ_MODE=legacy`` issues the same queries and raises the
same ``SKILL_NOT_FOUND``. Only shadow/enforce read candidate rows
(``candidates.candidate_predicate``: owned, shared through an active grant, or a
platform skill, all within the current org) so OpenFGA can be asked about skills
the caller does not own. Another organization's row stays 404 (8.4) even when granted.
"""

from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.elements import ColumnElement

from app.authz.candidates import candidate_predicate
from app.authz.org_context import OrgContext
from app.authz.principal import Principal
from app.authz.private_actor import require_private_actor
from app.authz.resource_lifecycle import (
    LifecycleChange,
    on_resource_created,
    on_resource_deleted,
)
from app.authz.service import AuthzService, Verdict
from app.error_codes import skill_not_found
from app.models.skill import Skill
from app.skills import service as skill_service

SKILL_MIN_VISIBLE = "can_use"  # consumer is the widest skill relation (model.fga)
CREATE_SKILL = "cap_create_skill"


def _candidate_predicate(context: OrgContext) -> ColumnElement[bool]:
    # candidate_predicate keeps shared rows in the current org (8.4: other org -> 404).
    return candidate_predicate(Skill, "skill", context, platform=True)


async def _candidate(db: AsyncSession, skill_id: UUID, context: OrgContext) -> Skill | None:
    await require_private_actor(db, context)
    return await db.scalar(
        select(Skill)
        .where(Skill.id == skill_id, _candidate_predicate(context))
        .execution_options(autoflush=False)
    )


def _resource_org(skill: Skill | None, context: OrgContext) -> UUID | None:
    if skill is None:
        return None
    # Platform skills reach the org only through allowed_org (OpenFGA's in_boundary);
    # an org row keeps its own org, so another org's skill is 404 before any check.
    return context.org_id if skill.is_system and skill.org_id is None else skill.org_id


async def authorize_skill(
    db: AsyncSession,
    authz: AuthzService,
    context: OrgContext,
    skill_id: UUID,
    permission: str,
) -> Skill:
    """The skill when ``permission`` holds; 404 ``SKILL_NOT_FOUND`` / 403 per 8.4."""
    obj = f"skill:{skill_id}"
    if authz.enforces("skill"):
        skill = await _candidate(db, skill_id, context)
        await authz.require(
            Principal.for_user(context),
            permission,
            obj,
            min_visible=SKILL_MIN_VISIBLE,
            resource_org_id=_resource_org(skill, context),
            legacy=Verdict.NOT_FOUND,  # unused: the type is enforced
            not_found=skill_not_found(),
        )
        assert skill is not None  # noqa: S101 - require raised for a missing row
        return skill
    owned = await skill_service.get_skill(db, skill_id, context)
    found = owned
    if owned is None and authz.mode != "legacy":
        found = await _candidate(db, skill_id, context)  # shadow sample only
    await authz.require(
        Principal.for_user(context),
        permission,
        obj,
        min_visible=SKILL_MIN_VISIBLE,
        resource_org_id=_resource_org(found, context),
        legacy=Verdict.ALLOW if owned is not None else Verdict.NOT_FOUND,
        not_found=skill_not_found(),
    )
    assert owned is not None  # noqa: S101 - require raised for NOT_FOUND
    return owned


async def visible_skills(
    db: AsyncSession,
    authz: AuthzService,
    context: OrgContext,
    *,
    kind: str | None = None,
    query: str | None = None,
) -> list[Skill]:
    """``GET /api/skills``: candidates(skill) confirmed with ``can_use`` (legacy order kept)."""
    legacy = [] if authz.enforces("skill") else await _legacy_list(db, context, kind, query)
    if authz.mode == "legacy":
        return legacy
    extra = await _candidate_list(db, context, kind, query)
    known = {skill.id for skill in legacy}
    rows = [*legacy, *(skill for skill in extra if skill.id not in known)]
    kept = await authz.filter_visible(
        Principal.for_user(context),
        "can_use",
        [f"skill:{skill.id}" for skill in rows],
        legacy=[f"skill:{skill.id}" for skill in legacy],
    )
    allowed = set(kept)
    return [skill for skill in rows if f"skill:{skill.id}" in allowed]


async def _legacy_list(
    db: AsyncSession, context: OrgContext, kind: str | None, query: str | None
) -> list[Skill]:
    return await skill_service.list_skills(db, context, kind=kind, query=query)


async def _candidate_list(
    db: AsyncSession, context: OrgContext, kind: str | None, query: str | None
) -> list[Skill]:
    await require_private_actor(db, context)
    stmt = select(Skill).where(_candidate_predicate(context))
    if kind:
        stmt = stmt.where(Skill.kind == kind)
    if query:
        stmt = stmt.where(func.lower(Skill.name).like(f"%{query.strip().lower()}%"))
    stmt = stmt.order_by(Skill.last_modified_at.desc(), Skill.created_at.desc())
    return list((await db.scalars(stmt.execution_options(autoflush=False))).all())


async def require_create_skill(authz: AuthzService, context: OrgContext) -> None:
    """``CAP(cap_create_skill)``; every member may create skills today (legacy allow)."""
    await authz.require_cap(context, CREATE_SKILL, legacy=True)


async def authorize_builder_target(
    db: AsyncSession, authz: AuthzService, context: OrgContext, source_skill_id: UUID | None
) -> None:
    """Skill builder: a new skill needs ``cap_create_skill``; improving one ``can_edit``.

    In legacy mode the builder service's own private source lookup keeps deciding
    (and keeps its existing 404 timing), so nothing extra runs here.
    """
    if source_skill_id is None:
        await require_create_skill(authz, context)
    elif authz.mode != "legacy":
        await authorize_skill(db, authz, context, source_skill_id, "can_edit")


async def skill_created(db: AsyncSession, authz: AuthzService, skill: Skill) -> LifecycleChange:
    """Queue the new skill's tuples (legacy: no-op); ``apply_lifecycle`` after commit."""
    return await on_resource_created(db, skill, mode=authz.mode)


async def skill_deleted(db: AsyncSession, authz: AuthzService, skill: Skill) -> LifecycleChange:
    """Queue the deleted skill's tuple removals before ``db.delete`` (legacy: no-op)."""
    return await on_resource_deleted(db, skill, mode=authz.mode)
