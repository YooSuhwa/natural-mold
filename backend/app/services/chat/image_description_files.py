"""Private file-list captions projected from their exact message and receipt versions."""

from __future__ import annotations

import asyncio
import hashlib
from collections.abc import Sequence
from dataclasses import dataclass
from itertools import batched
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.authz.attachment_scope import private_attachment_predicate
from app.authz.org_context import OrgContext
from app.models.message_attachment import MessageAttachment
from app.schemas.conversation import (
    ImageDescriptionReady,
    ImageDescriptionUnavailable,
    ImageDescriptionUse,
)
from app.services.chat.attachment_inputs import AttachmentHistory
from app.services.chat.image_description_input import (
    InvalidDescriptionInput,
    matches_version,
    scoped_image_path,
)
from app.services.chat.image_description_provider import ImageDescriptionDenied
from app.services.chat.image_description_read_policy import resolve_image_description_read_policy
from app.services.chat.image_description_receipts import (
    ImageDescriptionReceiptKey,
    find_file_descriptions,
)
from app.services.chat.image_descriptions import description_unavailable, receipt_result
from app.storage.paths import ForeignTenantPathError


@dataclass(frozen=True, slots=True)
class _FileUse:
    attachment: MessageAttachment
    run_id: UUID
    message_id: str
    runtime_message_id: str | None
    ref: dict[str, Any]


def _verify_versions(
    files: dict[tuple[UUID, str], tuple[str, int]], tenant_id: UUID
) -> set[tuple[UUID, str]]:
    matches: set[tuple[UUID, str]] = set()
    for version, (stored_path, limit) in files.items():
        try:
            path = scoped_image_path(stored_path, tenant_id)
            if matches_version(path, limit, version[1]):
                matches.add(version)
        except (ForeignTenantPathError, InvalidDescriptionInput, OSError, ValueError):
            continue
    return matches


async def _current_versions(
    db: AsyncSession,
    context: OrgContext,
    conversation_id: UUID,
    originals: dict[UUID, MessageAttachment],
    verified: set[tuple[UUID, str]],
) -> set[tuple[UUID, str]]:
    """An upload may be unlinked while hashing or have a different storage binding now."""
    current: set[tuple[UUID, str]] = set()
    query = select(
        MessageAttachment.id,
        MessageAttachment.content_sha256,
        MessageAttachment.storage_path,
        MessageAttachment.mime_type,
        MessageAttachment.size_bytes,
        MessageAttachment.message_id,
    ).where(
        MessageAttachment.conversation_id == conversation_id,
        private_attachment_predicate(context),
    )
    for group in batched({asset_id for asset_id, _ in verified}, 500):
        rows = await db.execute(query.where(MessageAttachment.id.in_(group)))
        for asset_id, digest, path, mime, size, message_id in rows:
            asset = originals[asset_id]
            if (
                digest
                and (asset_id, digest) in verified
                and (path, mime, size, message_id)
                == (asset.storage_path, asset.mime_type, asset.size_bytes, asset.message_id)
            ):
                current.add((asset_id, digest))
    return current


async def file_image_description_uses(
    db: AsyncSession,
    *,
    context: OrgContext,
    conversation_id: UUID,
    attachments: Sequence[MessageAttachment],
    histories: dict[str, AttachmentHistory],
) -> dict[UUID, list[ImageDescriptionUse]]:
    uses = [
        _FileUse(attachment, entry[0], message_id, entry[1].get("runtime_message_id"), entry[1])
        for attachment in attachments
        if (history := histories.get(str(attachment.id)))
        for message_id, entry in history.descriptions.items()
        if entry is not None
    ]
    if not uses:
        return {}

    outcomes: dict[int, ImageDescriptionReady | ImageDescriptionUnavailable] = {}
    receipt_keys: dict[int, ImageDescriptionReceiptKey] = {}
    versions: dict[tuple[UUID, str], tuple[str, int]] = {}
    try:
        policy = await resolve_image_description_read_policy(
            db,
            context=context,
            conversation_id=conversation_id,
            run_ids={use.run_id for use in uses},
        )
        for position, use in enumerate(uses):
            attachment, ref = use.attachment, use.ref
            if error := ref.get("description_error"):
                outcomes[position] = description_unavailable(str(error))
                continue
            try:
                grant = policy.grant_for(use.run_id, attachment)
            except ImageDescriptionDenied as exc:
                outcomes[position] = description_unavailable(exc.code)
                continue
            digest = attachment.content_sha256
            question_sha = ref.get("question_sha256")
            if (
                not digest
                or digest != ref.get("content_sha256")
                or not isinstance(question_sha, str)
                or len(question_sha) != 64
                or str(grant.model_id) != ref.get("description_model")
                or grant.policy_fingerprint != ref.get("description_policy")
                or ref.get("description_run") != str(use.run_id)
            ):
                continue
            receipt_keys[position] = ImageDescriptionReceiptKey(
                attachment.id,
                digest,
                question_sha,
                grant.model_id,
                hashlib.sha256(grant.policy_fingerprint.encode("utf-8")).hexdigest(),
            )
            versions[attachment.id, digest] = (attachment.storage_path, grant.max_input_bytes)
        receipts = await find_file_descriptions(
            db,
            context=context,
            conversation_id=conversation_id,
            keys=set(receipt_keys.values()),
        )
        # Reuse one file-integrity check even when several branch questions refer to that version.
        completed_versions = {
            (key.attachment_id, key.content_sha256): versions[key.attachment_id, key.content_sha256]
            for key, result in receipts.items()
            if result.status == "completed"
        }
        verified = await asyncio.to_thread(_verify_versions, completed_versions, context.tenant_id)
        verified = await _current_versions(
            db,
            context,
            conversation_id,
            {asset.id: asset for asset in attachments},
            verified,
        )
        for position, key in receipt_keys.items():
            result = receipts.get(key)
            if result and (
                result.status != "completed" or (key.attachment_id, key.content_sha256) in verified
            ):
                outcomes[position] = receipt_result(result)
        # No positive decision cache outlives this request. Discard read results if an
        # administrator revoked an authorization or changed the binding during this scan.
        if not await policy.still_current(db):
            raise ImageDescriptionDenied("access_denied")
    except ImageDescriptionDenied as exc:
        outcomes = dict.fromkeys(range(len(uses)), description_unavailable(exc.code))

    projected: dict[UUID, list[ImageDescriptionUse]] = {}
    unavailable = description_unavailable("unavailable")
    for position, use in enumerate(uses):
        projected.setdefault(use.attachment.id, []).append(
            ImageDescriptionUse(
                message_id=use.message_id,
                runtime_message_id=use.runtime_message_id,
                image_description=outcomes.get(position, unavailable),
            )
        )
    return projected
