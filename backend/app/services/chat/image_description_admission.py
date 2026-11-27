"""Freeze the allowed image destination when an input is accepted.

A queued input can outlive organization and model changes. Workers may further
restrict its pinned destination, never add one that was absent at admission.
"""

from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent_runtime.attachment_inputs import MAX_IMAGES_PER_CALL
from app.authz.conversation_run_scope import private_run_predicate
from app.authz.org_context import OrgContext
from app.authz.scoping import private_scope_predicate
from app.models.conversation_run import ConversationRun
from app.models.conversation_run_input import JsonValue
from app.models.message_attachment import MessageAttachment
from app.models.model import Model
from app.services.chat.image_description_input import (
    InvalidDescriptionInput,
    read_version,
    scoped_image_path,
)
from app.services.chat.image_description_provider import (
    ImageDescriptionDenied,
    resolve_image_description_target,
    resolve_queued_image_description_target,
)
from app.storage.paths import ForeignTenantPathError

ADMISSION_KEY = "image_description_admission"
_DIGEST = re.compile("[0-9a-f]{64}")


@dataclass(frozen=True, slots=True)
class AcceptedImageDescription:
    model_id: UUID
    content_sha256: str
    policy_fingerprint: str


async def capture_admission(
    db: AsyncSession,
    *,
    context: OrgContext,
    conversation_id: UUID,
    root_model_id: str | None,
    attachment_ids: list[UUID],
    run_id: UUID | None = None,
) -> dict[str, JsonValue]:
    if not attachment_ids or not root_model_id:
        return {}
    try:
        root_id = UUID(root_model_id)
    except (ValueError, TypeError, AttributeError):
        return {}
    supports_vision = await db.scalar(select(Model.supports_vision).where(Model.id == root_id))
    if supports_vision is not False:
        return {}
    image_ids = set(
        await db.scalars(
            select(MessageAttachment.id).where(
                MessageAttachment.id.in_(attachment_ids),
                MessageAttachment.conversation_id == conversation_id,
                MessageAttachment.mime_type.in_(
                    ("image/png", "image/jpeg", "image/gif", "image/webp")
                ),
                private_scope_predicate(MessageAttachment, context),
            )
        )
    )
    ordered_ids = [item for item in dict.fromkeys(attachment_ids) if item in image_ids]
    images: dict[str, JsonValue] = {}
    for attachment_id in ordered_ids[-MAX_IMAGES_PER_CALL:]:
        try:
            if run_id is None:
                target = await resolve_queued_image_description_target(
                    db,
                    context=context,
                    conversation_id=conversation_id,
                    attachment_id=attachment_id,
                )
            else:
                target = await resolve_image_description_target(
                    db,
                    context=context,
                    run_id=run_id,
                    conversation_id=conversation_id,
                    attachment_id=attachment_id,
                )
            digest = target.attachment.content_sha256
            if digest is None:
                path = scoped_image_path(target.attachment.storage_path, context.tenant_id)
                _, digest = await asyncio.to_thread(read_version, path, target.max_input_bytes)
                target.attachment.content_sha256 = digest
            if _DIGEST.fullmatch(digest):
                images[str(attachment_id)] = {
                    "content_sha256": digest,
                    "model_id": str(target.model_id),
                    "policy": target.policy_fingerprint,
                }
        except (ImageDescriptionDenied, ForeignTenantPathError, InvalidDescriptionInput):
            continue
    return {"root_model_id": str(root_id), "images": images} if images else {}


async def freeze_direct_admission(
    db: AsyncSession,
    *,
    context: OrgContext,
    conversation_id: UUID,
    run_id: UUID,
    root_model_id: str | None,
    attachment_ids: list[UUID],
) -> None:
    if not attachment_ids:
        return
    run = await db.scalar(
        select(ConversationRun)
        .where(
            ConversationRun.id == run_id,
            ConversationRun.conversation_id == conversation_id,
            private_run_predicate(context),
        )
        .with_for_update(of=ConversationRun)
        .execution_options(populate_existing=True)
    )
    if run is None or ADMISSION_KEY in (run.metadata_json or {}):
        return
    guard = await capture_admission(
        db,
        context=context,
        conversation_id=conversation_id,
        run_id=run_id,
        root_model_id=root_model_id,
        attachment_ids=attachment_ids,
    )
    run.metadata_json = {**(run.metadata_json or {}), ADMISSION_KEY: guard}


async def accepted_image_description(
    db: AsyncSession,
    *,
    context: OrgContext,
    conversation_id: UUID,
    run_id: UUID,
    root_model_id: str | None,
    attachment_id: UUID,
) -> AcceptedImageDescription | None:
    """Return immutable server metadata, never dispatch or refill a missing pin."""
    metadata = await db.scalar(
        select(ConversationRun.metadata_json).where(
            ConversationRun.id == run_id,
            ConversationRun.conversation_id == conversation_id,
            private_run_predicate(context),
        )
    )
    if not isinstance(metadata, dict):
        return None
    guard = metadata.get(ADMISSION_KEY)
    if (
        not root_model_id
        or not isinstance(guard, dict)
        or guard.get("root_model_id") != root_model_id
    ):
        return None
    images = guard.get("images")
    pin = images.get(str(attachment_id)) if isinstance(images, dict) else None
    if not isinstance(pin, dict):
        return None
    digest, fingerprint, model = pin.get("content_sha256"), pin.get("policy"), pin.get("model_id")
    if not isinstance(digest, str) or not _DIGEST.fullmatch(digest):
        return None
    if (
        not isinstance(fingerprint, str)
        or not _DIGEST.fullmatch(fingerprint)
        or not isinstance(model, str)
    ):
        return None
    try:
        model_id = UUID(model)
    except ValueError:
        return None
    return AcceptedImageDescription(model_id, digest, fingerprint)
