"""Exercise the attachment graph boundary and the read-only private chat file projection."""

from __future__ import annotations

import asyncio
import io
from pathlib import Path
from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from langchain.agents.middleware import ModelRequest
from langchain_core.messages import HumanMessage
from PIL import Image, PngImagePlugin
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app import database
from app.agent_runtime.attachment_inputs import ATTACHMENT_REFS_KEY, AttachmentInputMiddleware
from app.config import settings
from app.models.agent import Agent
from app.models.chat_image_description import ChatImageDescription
from app.models.conversation_run import ConversationRun
from app.models.message_attachment import MessageAttachment
from app.models.organization import OrganizationMember
from app.models.token_usage import TokenUsage
from app.services.chat import image_description_provider as provider
from app.services.chat.attachment_inputs import prepare_attachment_inputs
from app.services.chat.attachments import list_conversation_files
from app.services.chat.image_description_admission import freeze_direct_admission
from app.services.chat.image_description_input import question_digest
from tests.conftest import TestSession

if TYPE_CHECKING:
    from tests.authz.image_description_fixture import Case

pytest_plugins = ["tests.authz.image_description_fixture"]

QUESTION = "도형의 색은 무엇인가요?"


async def _prepare(
    db: AsyncSession, case: Case, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> tuple[dict[str, str], AttachmentInputMiddleware]:
    monkeypatch.setattr(database, "async_session", TestSession)
    monkeypatch.setattr(settings, "upload_dir", str(tmp_path))
    path = tmp_path / "tenants" / str(case.context.tenant_id) / "sample.png"
    path.parent.mkdir(parents=True)
    metadata = PngImagePlugin.PngInfo()
    metadata.add_text("comment", "private metadata for test")
    Image.new("RGB", (24, 24), "blue").save(path, pnginfo=metadata)
    case.attachment.storage_path = str(path)
    case.attachment.size_bytes = path.stat().st_size
    case.attachment.message_id = str(uuid4())
    case.attachment.url = f"/api/uploads/{case.attachment.id}"
    await db.commit()
    root = await db.get(Agent, case.run.agent_id)
    assert root is not None
    await freeze_direct_admission(
        db,
        context=case.context,
        conversation_id=case.run.conversation_id,
        run_id=case.run.id,
        root_model_id=str(root.model_id),
        attachment_ids=[case.attachment.id],
    )
    await db.commit()
    payload, inputs = await prepare_attachment_inputs(
        db,
        run_id=case.run.id,
        conversation_id=case.run.conversation_id,
        context=case.context,
        model_id=str(root.model_id),
        attachment_ids=[case.attachment.id],
        input_payload={
            "messages": [{"role": "user", "id": case.attachment.message_id, "content": QUESTION}]
        },
        tag_input=True,
    )
    assert inputs is not None
    ref = payload["messages"][0]["additional_kwargs"][ATTACHMENT_REFS_KEY][0]
    return ref, AttachmentInputMiddleware(inputs)


async def _send(middleware: AttachmentInputMiddleware, ref: dict[str, str], question: str) -> str:
    message = HumanMessage(content=question, additional_kwargs={ATTACHMENT_REFS_KEY: [ref]})
    request = ModelRequest(model=None, messages=[message], system_message=None)  # type: ignore[arg-type]

    async def capture(sent: Any) -> str:
        (expanded,) = sent.messages
        assert ATTACHMENT_REFS_KEY not in expanded.additional_kwargs
        assert expanded.content[0] == {"type": "text", "text": question}
        assert all(part["type"] == "text" for part in expanded.content)
        return expanded.content[-1]["text"]

    result = await middleware.awrap_model_call(request, capture)
    assert message.content == question
    return result


async def _files(db: AsyncSession, case: Case) -> Any:
    (item,) = await list_conversation_files(
        db,
        context=case.context,
        conversation_id=case.run.conversation_id,
        user_id=case.context.user_id,
    )
    return item


async def test_render_replay_and_revocation(
    case: Case, db: AsyncSession, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ref, middleware = await _prepare(db, case, tmp_path, monkeypatch)
    assert ref["form"] == "image_description" and ref["question_sha256"] == question_digest(
        QUESTION
    )
    assert "private metadata" not in str(ref)
    wire = AsyncMock(return_value=("파란색 사각형입니다.", 19, 7))
    monkeypatch.setattr(provider, "open_values", AsyncMock(return_value={"api_key": "test-dummy"}))
    monkeypatch.setattr(provider, "send_image_description", wire)
    # Merely opening a file list (including reconnect before first model call) cannot start a run.
    assert (await _files(db, case)).image_description.status == "unavailable"
    wire.assert_not_awaited()
    seen = await _send(middleware, ref, QUESTION)
    assert "파란색" in seen and case.attachment.url in seen
    assert await _send(middleware, ref, QUESTION) == seen
    assert "파란색" not in await _send(middleware, ref, "다른 질문")
    assert wire.await_args is not None
    with Image.open(io.BytesIO(wire.await_args.kwargs["image"])) as image:
        pixel = image.convert("RGB").getpixel((4, 4))
        assert isinstance(pixel, tuple) and pixel[2] > 220 and pixel[0] < 30
        assert "comment" not in image.info
    wire.assert_awaited_once()
    assert wire.await_args.kwargs["question"] == QUESTION
    item = await _files(db, case)
    assert item.image_description.text == "파란색 사각형입니다."
    assert item.preview_url == case.attachment.url
    receipt = (await db.scalars(select(ChatImageDescription))).one()
    assert receipt.run_id == case.run.id
    assert (receipt.input_tokens, receipt.output_tokens) == (19, 7)
    case.org.settings = {}
    await db.commit()
    assert (await _files(db, case)).image_description.reason == "policy_denied"
    assert "파란색" not in await _send(middleware, ref, QUESTION)
    wire.assert_awaited_once()


@pytest.mark.parametrize("change", ["policy", "content", "mime", "cancel", "timeout"])
async def test_denied_or_failed_image_leaves_text_chat_usable(
    case: Case, db: AsyncSession, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, change: str
) -> None:
    ref, middleware = await _prepare(db, case, tmp_path, monkeypatch)
    wire = AsyncMock(side_effect=provider.ImageDescriptionWireError("provider_timeout"))
    monkeypatch.setattr(provider, "open_values", AsyncMock(return_value={"api_key": "test-dummy"}))
    monkeypatch.setattr(provider, "send_image_description", wire)
    if change == "policy":
        case.org.settings = {}
    if change == "content":
        await asyncio.to_thread(Path(case.attachment.storage_path).write_bytes, b"replaced image")
    if change == "mime":
        case.attachment.mime_type = "image/jpeg"
    if change == "cancel":
        case.run.status = "canceling"
    await db.commit()
    result = await _send(middleware, ref, QUESTION)
    assert "파란색" not in result
    assert wire.await_count == (1 if change == "timeout" else 0)
    assert await _send(middleware, ref, QUESTION) == result
    assert wire.await_count == (1 if change == "timeout" else 0)


async def test_reused_image_captions_follow_their_own_questions(
    case: Case, db: AsyncSession, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    original, original_middleware = await _prepare(db, case, tmp_path, monkeypatch)
    wire = AsyncMock(side_effect=[("파란색", 10, 3), ("사각형", 11, 4)])
    monkeypatch.setattr(provider, "open_values", AsyncMock(return_value={"api_key": "test-dummy"}))
    monkeypatch.setattr(provider, "send_image_description", wire)
    assert "파란색" in await _send(original_middleware, original, QUESTION)
    first_id = case.attachment.message_id
    assert first_id is not None
    case.run.is_active = False
    case.run.status = "completed"
    await db.flush()
    fork = ConversationRun(
        conversation_id=case.run.conversation_id,
        agent_id=case.run.agent_id,
        org_id=case.context.org_id,
        tenant_id=case.context.tenant_id,
        user_id=case.context.user_id,
        resource_owner_user_id=case.context.user_id,
        credential_subject_user_id=case.context.user_id,
        invoker_type="user",
        source="edit",
        run_source="CHAT",
        is_active=True,
        status="running",
    )
    db.add(fork)
    await db.commit()
    root = await db.get(Agent, case.run.agent_id)
    assert root is not None
    question = "도형의 모양은 무엇인가요?"
    await freeze_direct_admission(
        db,
        context=case.context,
        conversation_id=fork.conversation_id,
        run_id=fork.id,
        root_model_id=str(root.model_id),
        attachment_ids=[case.attachment.id],
    )
    await db.commit()
    # LangGraph also accepts a new human message without an id; the server supplies
    # the stable id the history renderer will receive on reconnect.
    payload, inputs = await prepare_attachment_inputs(
        db,
        run_id=fork.id,
        conversation_id=fork.conversation_id,
        context=case.context,
        model_id=str(root.model_id),
        attachment_ids=[case.attachment.id],
        input_payload={"messages": [{"role": "user", "content": question}]},
        tag_input=True,
    )
    assert inputs is not None
    edited = payload["messages"][0]
    assert edited["id"] != first_id
    ref = edited["additional_kwargs"][ATTACHMENT_REFS_KEY][0]
    assert "사각형" in await _send(AttachmentInputMiddleware(inputs), ref, question)
    file = await _files(db, case)
    assert file.message_id == first_id
    assert file.image_description.text == "파란색"
    assert {
        item.message_id: item.image_description.text for item in file.image_description_uses
    } == {
        first_id: "파란색",
        edited["id"]: "사각형",
    }
    assert wire.await_count == 2

    # An API client can reuse a message id on different branches. Withhold both
    # captions if the message identity alone could refer to either question.
    await db.refresh(fork)
    metadata = fork.metadata_json or {}
    fork.metadata_json = {
        **metadata,
        "attachment_inputs": {
            **metadata["attachment_inputs"],
            "message_id": first_id,
        },
    }
    await db.commit()
    ambiguous = await _files(db, case)
    assert ambiguous.image_description is None
    assert ambiguous.image_description_uses == []
    assert wire.await_count == 2


@pytest.mark.parametrize("change", ["delete_upload", "revoke_membership"])
async def test_billed_call_settles_after_access_changes_during_dispatch(
    case: Case, db: AsyncSession, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, change: str
) -> None:
    ref, middleware = await _prepare(db, case, tmp_path, monkeypatch)
    marked_text = "escaped private caption"

    async def reply_after_change(**_kwargs: Any) -> tuple[str, int, int]:
        async with TestSession() as admin:
            if change == "delete_upload":
                await admin.execute(
                    delete(MessageAttachment).where(MessageAttachment.id == case.attachment.id)
                )
            else:
                await admin.execute(
                    delete(OrganizationMember).where(
                        OrganizationMember.org_id == case.context.org_id,
                        OrganizationMember.user_id == case.context.user_id,
                    )
                )
            await admin.commit()
        return marked_text, 19, 7

    monkeypatch.setattr(provider, "open_values", AsyncMock(return_value={"api_key": "test-dummy"}))
    wire = AsyncMock(side_effect=reply_after_change)
    monkeypatch.setattr(provider, "send_image_description", wire)
    assert marked_text not in await _send(middleware, ref, QUESTION)
    wire.assert_awaited_once()
    async with TestSession() as audit:
        usage = (
            await audit.scalars(
                select(TokenUsage).where(TokenUsage.auxiliary_run_id == case.run.id)
            )
        ).one()
        assert (usage.prompt_tokens, usage.completion_tokens) == (19, 7)
        assert usage.estimated_cost is not None and usage.estimated_cost > 0
        assert not await audit.scalar(
            select(ChatImageDescription.id).where(ChatImageDescription.description == marked_text)
        )
