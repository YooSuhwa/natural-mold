"""Server side of attachments as model input (feature plan v2 1A).

``prepare_attachment_inputs`` runs once per run, before the graph starts. It loads
this conversation's attachments and the run model's vision support for the
middleware. For a new input it also decides each attachment's form from its mime
type and that model, tags the input's human message with the references, and
records the result on the run row (``attachment_inputs``).
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent_runtime.attachment_inputs import (
    AttachmentFile,
    AttachmentInputs,
    attachment_form,
    attachment_manifest,
    attachment_ref,
    tag_input_with_attachments,
)
from app.authz.conversation_run_scope import private_run_predicate
from app.authz.org_context import OrgContext
from app.authz.scoping import private_scope_predicate
from app.models.conversation_run import ConversationRun
from app.models.message_attachment import MessageAttachment
from app.models.model import Model
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


async def recorded_attachment_forms(
    db: AsyncSession, *, conversation_id: uuid.UUID, context: OrgContext
) -> dict[str, str]:
    """Each attachment's form from the run records; a later run (edit, regenerate) wins."""

    records = await db.scalars(
        select(ConversationRun.metadata_json)
        .where(ConversationRun.conversation_id == conversation_id, private_run_predicate(context))
        .order_by(ConversationRun.created_at, ConversationRun.id)
    )
    forms: dict[str, str] = {}
    for metadata in records:
        record = (metadata or {}).get(RUN_METADATA_KEY)
        items = record.get("items") if isinstance(record, dict) else None
        for item in items if isinstance(items, list) else []:
            if isinstance(item, dict) and isinstance(item.get("id"), str):
                forms[item["id"]] = str(item.get("form"))
    return forms


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
        if refs:
            record = attachment_manifest(refs, supports_vision=supports_vision)
            await _record_on_run(db, run_id, context, record)
        input_payload = tag_input_with_attachments(input_payload, refs)
    files = _files(rows)
    return input_payload, AttachmentInputs(files, supports_vision) if files else None
