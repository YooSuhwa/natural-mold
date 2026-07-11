from __future__ import annotations

from fastapi import Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.dependencies import CurrentUser
from app.marketplace import origin_service
from app.marketplace.schemas import (
    MarketplaceInstallationSummary,
    ResourcePublicationSummaryOut,
)
from app.models.marketplace import MarketplaceInstallation
from app.models.skill import Skill
from app.models.skill_revision import SkillRevision
from app.schemas.skill import SkillResponse
from app.services import audit_service, skill_revision_audit
from app.services.skill_response_enrichment import (
    agent_link_counts_by_skill,
    build_skill_quality_map,
)


async def record_skill_audit(
    db: AsyncSession,
    *,
    user: CurrentUser,
    request: Request,
    action: str,
    skill: Skill,
    metadata: dict[str, object] | None = None,
) -> None:
    await audit_service.record_event(
        db,
        actor_type="user",
        actor_user_id=user.id,
        actor_email_snapshot=user.email,
        owner_user_id=user.id,
        owner_email_snapshot=user.email,
        action=action,
        target_type="skill",
        target_id=skill.id,
        target_name_snapshot=skill.name,
        target_owner_user_id=user.id,
        outcome="success",
        request=request,
        metadata={
            "kind": skill.kind,
            "slug": skill.slug,
            "version": skill.version,
            "size_bytes": skill.size_bytes,
            **(metadata or {}),
        },
    )


async def serialize_skill(db: AsyncSession, skill: Skill, user: CurrentUser) -> SkillResponse:
    origin = origin_service.derive_origin_summary_for_skill(skill, user)
    publication = await origin_service.derive_publication_summary_for_skill(db, skill)
    response = SkillResponse.model_validate(skill)
    quality = (await build_skill_quality_map(db, user=user, skills=[skill])).get(skill.id)
    if quality is not None:
        response.latest_evaluation_summary = quality.latest_evaluation_summary
        response.health = quality.health
    link_counts = await agent_link_counts_by_skill(db, user_id=user.id, skill_ids=[skill.id])
    response.used_by_count = link_counts.get(skill.id, 0)
    response.origin_summary = origin
    response.publication_summary = publication
    response.installation = await _derive_installation(db, skill)
    return response


async def serialize_skills(
    db: AsyncSession,
    skills: list[Skill],
    user: CurrentUser,
) -> list[SkillResponse]:
    publications = await origin_service.bulk_derive_publication_summaries_for_skills(db, skills)
    installations = await origin_service.bulk_derive_skill_installation_summaries(db, skills)
    quality_by_skill = await build_skill_quality_map(db, user=user, skills=skills)
    link_counts = await agent_link_counts_by_skill(
        db, user_id=user.id, skill_ids=[skill.id for skill in skills]
    )
    responses: list[SkillResponse] = []
    for skill in skills:
        response = SkillResponse.model_validate(skill)
        quality = quality_by_skill.get(skill.id)
        if quality is not None:
            response.latest_evaluation_summary = quality.latest_evaluation_summary
            response.health = quality.health
        response.used_by_count = link_counts.get(skill.id, 0)
        response.origin_summary = origin_service.derive_origin_summary_for_skill(skill, user)
        response.publication_summary = publications.get(
            skill.id,
            ResourcePublicationSummaryOut(state="not_published"),
        )
        response.installation = installations.get(skill.id)
        responses.append(response)
    return responses


async def record_revision_create_audits(
    db: AsyncSession,
    *,
    user: CurrentUser,
    request: Request,
    baseline: SkillRevision | None,
    revision: SkillRevision,
) -> None:
    if baseline is not None:
        await skill_revision_audit.record_revision_create_audit(
            db,
            user=user,
            request=request,
            revision=baseline,
        )
    await skill_revision_audit.record_revision_create_audit(
        db,
        user=user,
        request=request,
        revision=revision,
    )


async def _derive_installation(
    db: AsyncSession,
    skill: Skill,
) -> MarketplaceInstallationSummary | None:
    if skill.source_marketplace_item_id is None:
        return None
    stmt = (
        select(MarketplaceInstallation)
        .where(MarketplaceInstallation.installed_skill_id == skill.id)
        .limit(1)
    )
    row = (await db.execute(stmt)).scalar_one_or_none()
    if row is None:
        return None
    return MarketplaceInstallationSummary(
        installed=row.install_status != "uninstalled",
        installation_id=row.id,
        installed_resource_id=row.installed_skill_id,
        status=row.install_status,  # type: ignore[arg-type]
        update_available=False,
        dirty=bool(row.is_dirty or skill.is_dirty),
    )
