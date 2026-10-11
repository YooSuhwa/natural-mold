"""Credential binding children inherit the persisted private Skill boundary."""

from typing import Protocol
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Mapped
from sqlalchemy.sql.elements import ColumnElement

from app.authz.org_context import OrgContext
from app.authz.private_actor import PrivateResourceActor, resolve_private_actor
from app.authz.scoping import private_scope_predicate
from app.error_codes import skill_not_found
from app.exceptions import AppError
from app.models.marketplace import SkillCredentialBinding
from app.models.skill import Skill


async def require_private_skill(db: AsyncSession, skill: Skill, context: OrgContext) -> None:
    found = await db.scalar(
        select(Skill.id)
        .where(
            Skill.id == skill.id,
            private_scope_predicate(Skill, context),
        )
        .execution_options(autoflush=False)
    )
    if found is None:
        raise skill_not_found()
    try:
        await resolve_private_actor(
            db, PrivateResourceActor(context.user_id, context.org_id, context.tenant_id)
        )
    except AppError:
        raise skill_not_found() from None


class PrivateSkillChild(Protocol):
    user_id: Mapped[UUID]
    skill_id: Mapped[UUID]


def private_skill_child_predicate(
    model: type[PrivateSkillChild], context: OrgContext
) -> ColumnElement[bool]:
    return (
        select(Skill.id)
        .where(
            Skill.id == model.skill_id,
            Skill.user_id == model.user_id,
            private_scope_predicate(Skill, context),
        )
        .exists()
    )


def skill_binding_scope_predicate(context: OrgContext) -> ColumnElement[bool]:
    return private_skill_child_predicate(SkillCredentialBinding, context)
