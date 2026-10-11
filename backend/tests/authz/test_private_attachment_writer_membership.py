"""Attachment writer siblings must refresh captured authority before effects."""

from __future__ import annotations

from io import BytesIO
from pathlib import Path
from typing import TYPE_CHECKING, Literal, assert_never

import pytest
from anyio import Path as AsyncPath
from fastapi import UploadFile
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.datastructures import Headers

from app.config import settings
from app.exceptions import AppError
from app.models.message_attachment import MessageAttachment
from app.models.organization import OrganizationMember
from app.routers import uploads
from app.services.chat import attachments

if TYPE_CHECKING:
    from tests.authz.private_scope_fixture import PrivateScopes
    from tests.authz.test_private_secret_collector import CollectorCase

pytest_plugins = ["tests.authz.test_private_attachment_scope"]
Operation = Literal["create", "link_conversation", "link_message"]


@pytest.mark.parametrize("revoked", [False, True])
@pytest.mark.parametrize("operation", ["create", "link_conversation", "link_message"])
async def test_attachment_writer_refreshes_scope(
    db: AsyncSession,
    private_scopes: PrivateScopes,
    private_upload: MessageAttachment,
    collector_case: CollectorCase,
    operation: Operation,
    revoked: bool,
) -> None:
    own = private_scopes.first
    conversation_id = collector_case.conversation.id
    if operation == "link_message":
        private_upload.conversation_id = conversation_id
        await db.commit()
    before_ids = set((await db.scalars(select(MessageAttachment.id))).all())
    if revoked:
        await db.execute(
            update(OrganizationMember)
            .where(
                OrganizationMember.user_id == own.user_id, OrganizationMember.org_id == own.org_id
            )
            .values(status="suspended")
        )
        await db.commit()
    denied = False
    try:
        match operation:
            case "create":
                await uploads.create_upload(
                    file=UploadFile(
                        file=BytesIO(b"F4_DUMMY_UPLOAD"),
                        filename="fixture.txt",
                        headers=Headers({"content-type": "text/plain"}),
                    ),
                    db=db,
                    context=own,
                    _csrf=None,
                )
            case "link_conversation":
                await attachments.link_attachments_to_conversation(
                    db,
                    context=own,
                    conversation_id=conversation_id,
                    attachment_ids=[private_upload.id],
                )
            case "link_message":
                await attachments.link_attachments_to_message(
                    db,
                    context=own,
                    conversation_id=conversation_id,
                    attachment_ids=[private_upload.id],
                    message_id="F4_DUMMY_MESSAGE",
                )
            case unreachable:
                assert_never(unreachable)
        await db.commit()
    except AppError:
        denied = True
    ids = set((await db.scalars(select(MessageAttachment.id))).all())
    row = await db.get(MessageAttachment, private_upload.id, populate_existing=True)
    assert row is not None
    changed = (
        ids != before_ids
        or (row.message_id == "F4_DUMMY_MESSAGE")
        or (operation == "link_conversation" and row.conversation_id == conversation_id)
    )
    assert (denied and not changed) if revoked else (not denied and changed)


async def test_owned_upload_creator_control(
    db: AsyncSession, private_scopes: PrivateScopes, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "upload_dir", str(tmp_path / "uploads"))
    upload = UploadFile(
        BytesIO(b"dummy"), filename="control.txt", headers=Headers({"content-type": "text/plain"})
    )
    row = await uploads.create_upload(file=upload, db=db, context=private_scopes.first, _csrf=None)
    assert row.org_id == private_scopes.first.org_id
    assert len([path async for path in AsyncPath(tmp_path / "uploads").iterdir()]) == 1
