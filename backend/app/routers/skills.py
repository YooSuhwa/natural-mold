"""Skills API — text + package CRUD, file tree, file content.

ADR-017 Slice A: every ``SkillResponse`` is wrapped through
``_serialize_skill`` which embeds origin / publication / installation
summaries from ``app.marketplace.origin_service``. List responses use the
single-skill path because Slice A has no users with installed marketplace
items yet; ``bulk_derive_*`` will be introduced when the catalog matures.
"""

from __future__ import annotations

import mimetypes
import uuid

from fastapi import APIRouter, Depends, Form, Query, Request, Response, UploadFile
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.dependencies import CurrentUser, get_current_user, get_db, verify_csrf
from app.error_codes import (
    invalid_file_path,
    invalid_skill_package,
    marketplace_secret_detected,
    skill_file_not_found,
    skill_not_found,
)
from app.marketplace import credential_requirements, origin_service
from app.marketplace.schemas import (
    MarketplaceInstallationSummary,
    ResourcePublicationSummaryOut,
)
from app.models.marketplace import MarketplaceInstallation
from app.models.skill import Skill
from app.schemas.skill import (
    SkillContentUpdate,
    SkillCreate,
    SkillCredentialBindingIn,
    SkillCredentialBindingOut,
    SkillCredentialRequirementOut,
    SkillFileEntry,
    SkillFileUpdate,
    SkillMetadataUpdate,
    SkillResponse,
    SkillTextContentResponse,
)
from app.services import audit_service
from app.skills import service as skill_service
from app.skills.inspector import SkillMetadataError
from app.skills.packager import PackageError

router = APIRouter(prefix="/api/skills", tags=["skills"])


async def _record_skill_audit(
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


async def _serialize_skill(db: AsyncSession, skill: Skill, user: CurrentUser) -> SkillResponse:
    """Build a ``SkillResponse`` with origin/publication/installation embed.

    ``origin_summary`` always present. ``publication_summary`` defaults
    to ``not_published`` when no publication link exists.
    ``installation`` is non-null only when the skill row was installed
    through the marketplace (``source_marketplace_item_id`` set).
    """

    origin = origin_service.derive_origin_summary_for_skill(skill, user)
    publication: ResourcePublicationSummaryOut = (
        await origin_service.derive_publication_summary_for_skill(db, skill)
    )
    installation: MarketplaceInstallationSummary | None = None
    if skill.source_marketplace_item_id is not None:
        stmt = (
            select(MarketplaceInstallation)
            .where(MarketplaceInstallation.installed_skill_id == skill.id)
            .limit(1)
        )
        row = (await db.execute(stmt)).scalar_one_or_none()
        if row is not None:
            installation = MarketplaceInstallationSummary(
                installed=row.install_status != "uninstalled",
                installation_id=row.id,
                installed_resource_id=row.installed_skill_id,
                status=row.install_status,  # type: ignore[arg-type]
                update_available=False,  # computed by catalog service; here for parity
                dirty=bool(row.is_dirty or skill.is_dirty),
            )
    response = SkillResponse.model_validate(skill)
    response.origin_summary = origin
    response.publication_summary = publication
    response.installation = installation
    return response


async def _serialize_skills(
    db: AsyncSession, skills: list[Skill], user: CurrentUser
) -> list[SkillResponse]:
    publications = await origin_service.bulk_derive_publication_summaries_for_skills(db, skills)
    installations = await origin_service.bulk_derive_skill_installation_summaries(db, skills)
    responses: list[SkillResponse] = []
    for skill in skills:
        response = SkillResponse.model_validate(skill)
        response.origin_summary = origin_service.derive_origin_summary_for_skill(skill, user)
        response.publication_summary = publications.get(
            skill.id,
            ResourcePublicationSummaryOut(state="not_published"),
        )
        response.installation = installations.get(skill.id)
        responses.append(response)
    return responses


@router.get("", response_model=list[SkillResponse])
async def list_skills(
    kind: str | None = Query(default=None, pattern="^(text|package)$"),
    q: str | None = Query(default=None, max_length=120),
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
):
    skills = await skill_service.list_skills(db, user.id, kind=kind, query=q)
    return await _serialize_skills(db, skills, user)


@router.post("", response_model=SkillResponse, status_code=201)
async def create_text_skill(
    data: SkillCreate,
    request: Request,
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
    _csrf: None = Depends(verify_csrf),
):
    try:
        skill = await skill_service.create_text_skill(
            db,
            user_id=user.id,
            name=data.name,
            slug=data.slug,
            description=data.description,
            content=data.content,
            version=data.version,
        )
    except SkillMetadataError as exc:
        raise invalid_skill_package(str(exc)) from None
    await db.commit()
    await db.refresh(skill)
    await _record_skill_audit(
        db,
        user=user,
        request=request,
        action="skill.create",
        skill=skill,
        metadata={"content_length": len(data.content)},
    )
    await db.commit()
    return await _serialize_skill(db, skill, user)


@router.post("/upload", response_model=SkillResponse, status_code=201)
async def upload_package_skill(
    file: UploadFile,
    request: Request,
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
    _csrf: None = Depends(verify_csrf),
):
    """Upload a ``.skill`` package (ZIP with SKILL.md frontmatter).

    ADR-017 Slice C — secret_scan runs after the extracted directory
    lands on disk. If secrets are found, the skill row + on-disk dir
    are rolled back before the response so a leak doesn't sit around
    waiting to be linked.
    """

    file_data = await file.read()
    try:
        skill = await skill_service.create_package_skill(
            db,
            user_id=user.id,
            zip_bytes=file_data,
        )
    except PackageError as exc:
        raise invalid_skill_package(str(exc)) from None

    # Spec §13.1 — gate the upload with the same secret_scan used by
    # publish. Imports + uploads share the surface so a leak can't
    # enter the system via either path.
    from pathlib import Path as _Path

    from app.config import settings
    from app.marketplace.secret_scan import scan_package
    from app.storage.paths import resolve_data_path

    skill_path = resolve_data_path(skill.storage_path)
    skills_root = (_Path(settings.data_root) / "skills").resolve()
    if not skill_path.is_relative_to(skills_root):
        await skill_service.delete_skill(db, skill)
        await db.rollback()
        raise invalid_skill_package("invalid skill storage path")

    findings = scan_package(skill_path)
    if findings:
        # Roll back the in-memory row + on-disk directory before raising.
        await skill_service.delete_skill(db, skill)
        await db.rollback()
        summary = ", ".join(f"{f.path} ({f.kind})" for f in findings[:5])
        raise marketplace_secret_detected(f"package contains potential secrets: {summary}")

    await db.commit()
    await db.refresh(skill)
    await _record_skill_audit(
        db,
        user=user,
        request=request,
        action="skill.create",
        skill=skill,
        metadata={
            "upload": True,
            "filename": file.filename,
            "package_file_count": len((skill.package_metadata or {}).get("files") or []),
        },
    )
    await db.commit()
    return await _serialize_skill(db, skill, user)


@router.get("/{skill_id}", response_model=SkillResponse)
async def get_skill(
    skill_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
):
    skill = await skill_service.get_skill(db, skill_id, user.id)
    if not skill:
        raise skill_not_found()
    return await _serialize_skill(db, skill, user)


@router.patch("/{skill_id}", response_model=SkillResponse)
async def patch_skill_metadata(
    skill_id: uuid.UUID,
    data: SkillMetadataUpdate,
    request: Request,
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
    _csrf: None = Depends(verify_csrf),
):
    skill = await skill_service.get_skill(db, skill_id, user.id)
    if not skill:
        raise skill_not_found()
    updated = await skill_service.update_metadata(
        db,
        skill=skill,
        name=data.name,
        description=data.description,
        version=data.version,
    )
    await db.commit()
    await db.refresh(updated)
    await _record_skill_audit(
        db,
        user=user,
        request=request,
        action="skill.update",
        skill=updated,
        metadata={"changed_fields": sorted(data.model_fields_set)},
    )
    await db.commit()
    return await _serialize_skill(db, updated, user)


@router.put("/{skill_id}/content", response_model=SkillResponse)
async def put_text_content(
    skill_id: uuid.UUID,
    data: SkillContentUpdate,
    request: Request,
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
    _csrf: None = Depends(verify_csrf),
):
    skill = await skill_service.get_skill(db, skill_id, user.id)
    if not skill:
        raise skill_not_found()
    if skill.kind != "text":
        raise invalid_skill_package("only text skills support content updates")
    try:
        updated = await skill_service.update_text_content(db, skill=skill, content=data.content)
    except SkillMetadataError as exc:
        raise invalid_skill_package(str(exc)) from None
    await db.commit()
    await db.refresh(updated)
    await _record_skill_audit(
        db,
        user=user,
        request=request,
        action="skill.content_update",
        skill=updated,
        metadata={"content_length": len(data.content)},
    )
    await db.commit()
    return await _serialize_skill(db, updated, user)


@router.get("/{skill_id}/content", response_model=SkillTextContentResponse)
async def get_text_content(
    skill_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
):
    skill = await skill_service.get_skill(db, skill_id, user.id)
    if not skill:
        raise skill_not_found()
    if skill.kind != "text":
        raise invalid_skill_package("only text skills expose plain content")
    content = await skill_service.read_text_content(skill)
    return SkillTextContentResponse(content=content)


@router.delete("/{skill_id}", status_code=204)
async def delete_skill(
    skill_id: uuid.UUID,
    request: Request,
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
    _csrf: None = Depends(verify_csrf),
):
    skill = await skill_service.get_skill(db, skill_id, user.id)
    if not skill:
        raise skill_not_found()
    await _record_skill_audit(
        db,
        user=user,
        request=request,
        action="skill.delete",
        skill=skill,
    )
    await skill_service.delete_skill(db, skill)
    await db.commit()


@router.get("/{skill_id}/files", response_model=list[SkillFileEntry])
async def list_skill_files(
    skill_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
):
    skill = await skill_service.get_skill(db, skill_id, user.id)
    if not skill:
        raise skill_not_found()
    return [
        SkillFileEntry(path=item.path, size=item.size, is_dir=item.is_dir)
        for item in skill_service.get_skill_files(skill)
    ]


@router.get("/{skill_id}/files/{file_path:path}")
async def get_skill_file(
    skill_id: uuid.UUID,
    file_path: str,
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
):
    skill = await skill_service.get_skill(db, skill_id, user.id)
    if not skill:
        raise skill_not_found()
    try:
        data = skill_service.get_file_bytes(skill, file_path)
    except FileNotFoundError:
        raise skill_file_not_found() from None
    except ValueError:
        raise invalid_file_path() from None
    media_type, _ = mimetypes.guess_type(file_path)
    return Response(content=data, media_type=media_type or "application/octet-stream")


# -- file-level mutations (M-SKILL1) ----------------------------------------


@router.put("/{skill_id}/files/{file_path:path}", response_model=SkillResponse)
async def put_skill_file(
    skill_id: uuid.UUID,
    file_path: str,
    data: SkillFileUpdate,
    request: Request,
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
    _csrf: None = Depends(verify_csrf),
):
    """Create or overwrite a single file in a package skill."""

    skill = await skill_service.get_skill(db, skill_id, user.id)
    if not skill:
        raise skill_not_found()
    if skill.kind != "package":
        raise invalid_skill_package("file-level mutations are only valid for package skills")
    try:
        updated = await skill_service.set_skill_file(
            db,
            skill=skill,
            rel_path=file_path,
            content=data.content.encode("utf-8"),
        )
    except SkillMetadataError as exc:
        raise invalid_skill_package(str(exc)) from None
    except ValueError as exc:
        raise invalid_file_path() from exc
    await db.commit()
    await db.refresh(updated)
    await _record_skill_audit(
        db,
        user=user,
        request=request,
        action="skill.file_update",
        skill=updated,
        metadata={"file_path": file_path, "content_length": len(data.content)},
    )
    await db.commit()
    return await _serialize_skill(db, updated, user)


@router.delete("/{skill_id}/files/{file_path:path}", response_model=SkillResponse)
async def delete_skill_file(
    skill_id: uuid.UUID,
    file_path: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
    _csrf: None = Depends(verify_csrf),
):
    """Delete a file (or directory) from a package skill. SKILL.md is protected."""

    skill = await skill_service.get_skill(db, skill_id, user.id)
    if not skill:
        raise skill_not_found()
    if skill.kind != "package":
        raise invalid_skill_package("file-level mutations are only valid for package skills")
    try:
        updated = await skill_service.delete_skill_file(db, skill=skill, rel_path=file_path)
    except ValueError as exc:
        raise invalid_file_path() from exc
    await db.commit()
    await db.refresh(updated)
    await _record_skill_audit(
        db,
        user=user,
        request=request,
        action="skill.file_delete",
        skill=updated,
        metadata={"file_path": file_path},
    )
    await db.commit()
    return await _serialize_skill(db, updated, user)


@router.post("/{skill_id}/files", response_model=SkillResponse, status_code=201)
async def upload_skill_file(
    skill_id: uuid.UUID,
    file: UploadFile,
    request: Request,
    rel_path: str = Form(..., description="Relative path inside the skill, eg 'scripts/run.py'"),
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
    _csrf: None = Depends(verify_csrf),
):
    """Upload a binary or text file (multipart) into a package skill."""

    skill = await skill_service.get_skill(db, skill_id, user.id)
    if not skill:
        raise skill_not_found()
    if skill.kind != "package":
        raise invalid_skill_package("file-level mutations are only valid for package skills")
    body = await file.read()
    try:
        updated = await skill_service.set_skill_file(
            db, skill=skill, rel_path=rel_path, content=body
        )
    except SkillMetadataError as exc:
        raise invalid_skill_package(str(exc)) from None
    except ValueError as exc:
        raise invalid_file_path() from exc
    await db.commit()
    await db.refresh(updated)
    await _record_skill_audit(
        db,
        user=user,
        request=request,
        action="skill.file_upload",
        skill=updated,
        metadata={
            "file_path": rel_path,
            "filename": file.filename,
            "content_length": len(body),
        },
    )
    await db.commit()
    return await _serialize_skill(db, updated, user)


# ---------------------------------------------------------------------------
# Credential binding API (ADR-017 Slice D / Spec §10.6)
# ---------------------------------------------------------------------------


@router.get(
    "/{skill_id}/credential-requirements",
    response_model=list[SkillCredentialRequirementOut],
)
async def get_skill_credential_requirements(
    skill_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
):
    """Public requirement list — empty when the skill has no requirements.

    No 404 on "empty" — only on "doesn't exist / not yours". Lets the
    UI render an empty state without an extra error path.
    """

    skill = await skill_service.get_skill(db, skill_id, user.id)
    if not skill:
        raise skill_not_found()
    return [
        SkillCredentialRequirementOut(
            key=r.key,
            definition_key=r.definition_key,
            required=r.required,
            label=r.label,
            description=r.description,
            fields=list(r.fields),
            injection=r.injection,  # type: ignore[arg-type]
            scope=r.scope,  # type: ignore[arg-type]
        )
        for r in credential_requirements.parse_requirements(skill)
    ]


@router.get(
    "/{skill_id}/credential-bindings",
    response_model=list[SkillCredentialBindingOut],
)
async def list_skill_credential_bindings(
    skill_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
):
    skill = await skill_service.get_skill(db, skill_id, user.id)
    if not skill:
        raise skill_not_found()
    rows = await credential_requirements.list_bindings(db, skill=skill, user=user)
    return [SkillCredentialBindingOut.model_validate(r) for r in rows]


@router.put(
    "/{skill_id}/credential-bindings/{requirement_key}",
    response_model=SkillCredentialBindingOut,
)
async def put_skill_credential_binding(
    skill_id: uuid.UUID,
    requirement_key: str,
    body: SkillCredentialBindingIn,
    request: Request,
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
    _csrf: None = Depends(verify_csrf),
):
    """Upsert binding for ``(skill, user, requirement_key)``.

    Validation goes through ``credential_requirements.validate_binding`` —
    rejects cross-user credentials (404), system credentials (400),
    definition_key mismatches (400), and unknown requirement keys (400).
    """

    skill = await skill_service.get_skill(db, skill_id, user.id)
    if not skill:
        raise skill_not_found()
    row = await credential_requirements.upsert_binding(
        db,
        skill=skill,
        user=user,
        requirement_key=requirement_key,
        credential_id=body.credential_id,
    )
    await db.commit()
    await db.refresh(row)
    await _record_skill_audit(
        db,
        user=user,
        request=request,
        action="skill.credential_binding_upsert",
        skill=skill,
        metadata={
            "requirement_key": requirement_key,
            "credential_id": str(body.credential_id),
        },
    )
    await db.commit()
    return SkillCredentialBindingOut.model_validate(row)


@router.delete(
    "/{skill_id}/credential-bindings/{requirement_key}",
    status_code=204,
)
async def delete_skill_credential_binding(
    skill_id: uuid.UUID,
    requirement_key: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
    _csrf: None = Depends(verify_csrf),
):
    skill = await skill_service.get_skill(db, skill_id, user.id)
    if not skill:
        raise skill_not_found()
    deleted = await credential_requirements.delete_binding(
        db,
        skill=skill,
        user=user,
        requirement_key=requirement_key,
    )
    if not deleted:
        # ``DELETE`` on a missing key — silently succeed (idempotent).
        # Returning 204 even for a no-op keeps client retry semantics
        # clean (rules/security.md — no extra enumeration channel).
        return Response(status_code=204)
    await _record_skill_audit(
        db,
        user=user,
        request=request,
        action="skill.credential_binding_delete",
        skill=skill,
        metadata={"requirement_key": requirement_key},
    )
    await db.commit()
    return Response(status_code=204)
