"""Server side of attachments as model input (feature plan v2 1A).

``prepare_attachment_inputs`` runs once per run, before the graph starts. It loads
this conversation's attachments and the run model's vision support for the
middleware. For a new input it also decides each attachment's form from its mime
type and that model, tags the input's human message with the references, and
records the result on the run row (``attachment_inputs``).
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent_runtime.attachment_inputs import (
    FORK_APPEND_COUNT,
    MAX_IMAGES_PER_CALL,
    AttachmentFile,
    AttachmentInputs,
    attachment_form,
    attachment_manifest,
    attachment_ref,
    last_attachment_message_id,
    last_attachment_question,
    tag_input_with_attachments,
)
from app.agent_runtime.message_utils import parse_msg_id
from app.authz.conversation_run_scope import private_run_predicate
from app.authz.org_context import OrgContext
from app.authz.scoping import private_scope_predicate
from app.models.conversation_run import ConversationRun
from app.models.message_attachment import MessageAttachment
from app.models.model import Model
from app.services.chat.image_descriptions import DescriptionRun, pin_description
from app.storage.paths import ForeignTenantPathError, upload_file_path

RUN_METADATA_KEY = "attachment_inputs"


async def model_supports_vision(db: AsyncSession, model_id: str | None) -> bool | None:
    if not model_id:
        return None
    try:
        parsed = uuid.UUID(model_id)
    except ValueError:
        return None
    return await db.scalar(select(Model.supports_vision).where(Model.id == parsed))


def _conversation_attachments_query(conversation_id: uuid.UUID, context: OrgContext) -> Any:
    return select(MessageAttachment).where(
        MessageAttachment.conversation_id == conversation_id,
        private_scope_predicate(MessageAttachment, context),
    )


async def conversation_has_attachments(
    db: AsyncSession, *, conversation_id: uuid.UUID, context: OrgContext
) -> bool:
    query = _conversation_attachments_query(conversation_id, context).limit(1)
    return (await db.execute(query)).scalars().first() is not None


@dataclass
class AttachmentHistory:
    form: str | None = None
    # A reused upload can have a different question and caption on each branch.
    # None marks an ambiguous message id, which must never receive a caption.
    descriptions: dict[str, tuple[uuid.UUID, dict[str, Any]] | None] = field(default_factory=dict)


async def recorded_attachment_inputs(
    db: AsyncSession, *, conversation_id: uuid.UUID, context: OrgContext
) -> dict[str, AttachmentHistory]:
    records = await db.execute(
        select(ConversationRun.id, ConversationRun.metadata_json[RUN_METADATA_KEY])
        .where(ConversationRun.conversation_id == conversation_id, private_run_predicate(context))
        .order_by(ConversationRun.created_at, ConversationRun.id)
    )
    histories: dict[str, AttachmentHistory] = {}
    for run_id, manifest in records:
        if not isinstance(manifest, dict) or not isinstance(manifest.get("items"), list):
            continue
        message_id = manifest.get("message_id")
        runtime_message_id = manifest.get("runtime_message_id")
        for ref in manifest["items"]:
            if not isinstance(ref, dict) or not isinstance(ref.get("id"), str):
                continue
            history = histories.setdefault(ref["id"], AttachmentHistory())
            history.form = ref.get("form")
            if history.form != "image_description" or not isinstance(message_id, str):
                continue
            latest = (run_id, {**ref, "runtime_message_id": runtime_message_id})
            if message_id not in history.descriptions:
                history.descriptions[message_id] = latest
                continue
            old = history.descriptions[message_id]
            same_version = old is not None and all(
                ref.get(part) and ref[part] == old[1].get(part)
                for part in (
                    "question_sha256",
                    "content_sha256",
                    "description_model",
                    "description_policy",
                )
            )
            history.descriptions[message_id] = latest if same_version else None
    return histories


def _files(rows: list[MessageAttachment]) -> dict[str, AttachmentFile]:
    files: dict[str, AttachmentFile] = {}
    for row in rows:
        try:
            path = upload_file_path(row.storage_path, row.tenant_id)
        except ForeignTenantPathError:
            continue
        files[str(row.id)] = AttachmentFile(
            id=str(row.id), filename=row.filename, mime_type=row.mime_type, path=path
        )
    return files


async def _record_on_run(
    db: AsyncSession, run_id: uuid.UUID, context: OrgContext, record: dict[str, Any]
) -> None:
    run = await db.scalar(
        select(ConversationRun).where(ConversationRun.id == run_id, private_run_predicate(context))
    )
    if run is not None:
        run.metadata_json = {**(run.metadata_json or {}), RUN_METADATA_KEY: record}
        await db.commit()


async def prepare_attachment_inputs(
    db: AsyncSession,
    *,
    run_id: uuid.UUID,
    conversation_id: uuid.UUID,
    context: OrgContext,
    model_id: str | None,
    attachment_ids: list[uuid.UUID] | None,
    input_payload: Any,
    tag_input: bool,
) -> tuple[Any, AttachmentInputs | None]:
    """Return the input (tagged when ``tag_input``) and the middleware's inputs."""

    query = _conversation_attachments_query(conversation_id, context)
    rows = list((await db.execute(query)).scalars())
    supports_vision = await model_supports_vision(db, model_id) if rows else None
    if tag_input:
        by_id = {row.id: row for row in rows}
        refs = [
            attachment_ref(
                attachment_id=str(row.id),
                filename=row.filename,
                mime_type=row.mime_type,
                form=attachment_form(row.mime_type, supports_vision=supports_vision),
            )
            for row in (by_id[item] for item in attachment_ids or [] if item in by_id)
        ]
        if refs and supports_vision is False:
            question = last_attachment_question(input_payload)
            images = [ref for ref in refs if ref["form"] == "image_no_vision"]
            for ref in images[-MAX_IMAGES_PER_CALL:]:
                await pin_description(
                    db,
                    ref=ref,
                    attachment=by_id[uuid.UUID(ref["id"])],
                    context=context,
                    conversation_id=conversation_id,
                    run_id=run_id,
                    root_model_id=model_id,
                    question=question,
                )
        described = any(ref["form"] == "image_description" for ref in refs)
        default_id = str(uuid.uuid5(run_id, "attachment-question")) if described else None
        input_payload = tag_input_with_attachments(
            input_payload, refs, default_message_id=default_id
        )
        if refs:
            record = attachment_manifest(refs, supports_vision=supports_vision)
            if described and (raw_id := last_attachment_message_id(input_payload)):
                record["message_id"] = str(parse_msg_id(raw_id, conversation_id, 0))
                if record["message_id"] != raw_id:
                    record["runtime_message_id"] = raw_id
            await _record_on_run(db, run_id, context, record)
    if isinstance(input_payload, dict) and FORK_APPEND_COUNT in input_payload:
        input_payload = {
            key: value for key, value in input_payload.items() if key != FORK_APPEND_COUNT
        }
    files = _files(rows)
    if not files:
        return input_payload, None
    resolver = DescriptionRun.for_run(context, conversation_id, run_id)
    return input_payload, AttachmentInputs(files, supports_vision, resolver.model_note)
