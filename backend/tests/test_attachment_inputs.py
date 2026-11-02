"""Chat attachments as model input (feature plan v2 1A)."""

from __future__ import annotations

import base64
import io
import os
import uuid
from pathlib import Path
from typing import Any

import pytest
from langchain.agents.middleware import ModelRequest
from langchain_core.messages import AIMessage, HumanMessage
from langchain_core.runnables import RunnableConfig
from PIL import Image
from pypdf import PdfWriter
from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject

from app.agent_runtime.attachment_inputs import (
    ATTACHMENT_REFS_KEY,
    MAX_IMAGE_BYTES,
    MAX_IMAGES_PER_CALL,
    MAX_TEXT_CHARS,
    AttachmentFile,
    AttachmentInputMiddleware,
    AttachmentInputs,
    attachment_form,
    attachment_ref,
    tag_input_with_attachments,
)
from app.agent_runtime.message_utils import convert_to_langchain_messages
from app.agent_runtime.prepared_run_input_policy import is_legacy_fallback
from app.authz.conversation_scope import require_conversation_agent_id
from app.authz.worker_conversation_scope import WorkerConversationTarget
from app.config import settings
from app.models.agent import Agent
from app.models.conversation import Conversation
from app.models.conversation_run import ConversationRun
from app.models.message_attachment import MessageAttachment
from app.models.model import Model
from app.services import conversation_run_service, conversation_run_worker
from app.services.chat.attachment_inputs import conversation_has_attachments
from tests.authz.run_fixture_config import run_fixture_config
from tests.authz.unit_scope_fixture import TEST_ORG_ID, TEST_TENANT_ID, unit_context
from tests.conftest import TEST_USER_ID, TestSession
from tests.integration._seed import seed_conversation_with_agent


def _png(size: tuple[int, int] = (4, 4), *, noise: bool = False) -> bytes:
    image = (
        Image.frombytes("RGB", size, os.urandom(size[0] * size[1] * 3))
        if noise
        else Image.new("RGB", size, (200, 10, 10))
    )
    out = io.BytesIO()
    image.save(out, format="PNG")
    return out.getvalue()


def _pdf(*pages: str, password: str | None = None) -> bytes:
    writer = PdfWriter()
    font = writer._add_object(
        DictionaryObject(
            {
                NameObject("/Type"): NameObject("/Font"),
                NameObject("/Subtype"): NameObject("/Type1"),
                NameObject("/BaseFont"): NameObject("/Helvetica"),
            }
        )
    )
    for text in pages:
        page = writer.add_blank_page(300, 300)
        if not text:
            continue
        page[NameObject("/Resources")] = DictionaryObject(
            {NameObject("/Font"): DictionaryObject({NameObject("/F1"): font})}
        )
        stream = DecodedStreamObject()
        stream.set_data(f"BT /F1 12 Tf 10 150 Td ({text}) Tj ET".encode())
        page[NameObject("/Contents")] = writer._add_object(stream)
    if password:
        writer.encrypt(password)
    out = io.BytesIO()
    writer.write(out)
    return out.getvalue()


def _file(tmp_path: Path, name: str, mime: str, data: bytes) -> AttachmentFile:
    path = tmp_path / f"{uuid.uuid4()}-{name}"
    path.write_bytes(data)
    return AttachmentFile(id=str(uuid.uuid4()), filename=name, mime_type=mime, path=path)


def _ref(attachment: AttachmentFile, form: str) -> dict[str, str]:
    return attachment_ref(
        attachment_id=attachment.id,
        filename=attachment.filename,
        mime_type=attachment.mime_type,
        form=form,  # type: ignore[arg-type]
    )


def _human(text: str, refs: list[dict[str, str]]) -> HumanMessage:
    return HumanMessage(
        content=text, id=f"h-{uuid.uuid4()}", additional_kwargs={ATTACHMENT_REFS_KEY: refs}
    )


def _call(middleware: AttachmentInputMiddleware, messages: list[Any]) -> list[Any]:
    seen: list[Any] = []

    def handler(request: ModelRequest) -> AIMessage:
        seen.extend(request.messages)
        return AIMessage(content="ok")

    request = ModelRequest(model=None, messages=messages, system_message=None)  # type: ignore[arg-type]
    middleware.wrap_model_call(request, handler)
    return seen


@pytest.mark.parametrize(
    ("mime", "vision", "form"),
    [
        ("image/png", True, "image"),
        ("image/png", False, "image_no_vision"),
        # Unknown support never sends an image: a text-only model would fail every turn.
        ("image/jpeg", None, "image_no_vision"),
        ("image/svg+xml", True, "unsupported"),
        ("text/plain; charset=utf-8", None, "text"),
        ("application/json", False, "text"),
        ("application/pdf", None, "pdf"),
        ("application/zip", True, "unsupported"),
    ],
)
def test_attachment_form(mime: str, vision: bool | None, form: str) -> None:
    assert attachment_form(mime, supports_vision=vision) == form


def test_tag_input_marks_last_human_and_drops_client_refs() -> None:
    forged = [{"id": str(uuid.uuid4()), "form": "image"}]
    ref = {"id": str(uuid.uuid4()), "filename": "a.txt", "mime_type": "text/plain", "form": "text"}
    payload = {
        "messages": [
            {"type": "human", "content": "old", ATTACHMENT_REFS_KEY: forged},
            {"type": "ai", "content": "reply"},
            {"type": "human", "content": "new", "additional_kwargs": {ATTACHMENT_REFS_KEY: forged}},
        ],
        "other": 1,
    }

    tagged = tag_input_with_attachments(payload, [ref])

    first, _, last = tagged["messages"]
    assert ATTACHMENT_REFS_KEY not in first
    assert last["additional_kwargs"][ATTACHMENT_REFS_KEY] == [ref]
    assert tagged["other"] == 1
    assert payload["messages"][0][ATTACHMENT_REFS_KEY] == forged  # input not mutated
    assert tag_input_with_attachments(payload, [])["messages"][2]["additional_kwargs"] == {}


def test_tag_input_handles_message_objects_and_legacy_lists() -> None:
    ref = {"id": str(uuid.uuid4()), "filename": "a.png", "mime_type": "image/png", "form": "image"}

    fork = tag_input_with_attachments({"messages": [HumanMessage(content="edit")]}, [ref])
    assert fork["messages"][0].additional_kwargs[ATTACHMENT_REFS_KEY] == [ref]

    legacy = tag_input_with_attachments([{"role": "user", "content": "hi"}], [ref])
    (message,) = convert_to_langchain_messages(legacy)
    assert message.content == "hi"
    assert message.additional_kwargs[ATTACHMENT_REFS_KEY] == [ref]


def test_middleware_expands_image_and_text_without_touching_state(tmp_path: Path) -> None:
    image = _file(tmp_path, "red.png", "image/png", _png())
    text = _file(tmp_path, "note.txt", "text/plain", "안녕 첨부".encode())
    archive = _file(tmp_path, "doc.zip", "application/zip", b"PK")
    state_message = _human(
        "look", [_ref(image, "image"), _ref(text, "text"), _ref(archive, "unsupported")]
    )
    middleware = AttachmentInputMiddleware(
        AttachmentInputs({item.id: item for item in (image, text, archive)}, True)
    )

    (sent,) = _call(middleware, [state_message])

    text_block, image_block, file_block, archive_note = sent.content
    assert text_block == {"type": "text", "text": "look"}
    assert image_block["type"] == "image"
    assert image_block["mime_type"] == "image/png"
    assert base64.b64decode(image_block["base64"]) == image.path.read_bytes()
    assert "안녕 첨부" in file_block["text"]
    assert "doc.zip" in archive_note["text"] and "application/zip" in archive_note["text"]
    assert sent.id == state_message.id
    assert ATTACHMENT_REFS_KEY not in sent.additional_kwargs  # not sent to the provider
    assert state_message.content == "look"  # the checkpointed message keeps references only


def test_middleware_rechecks_vision_and_missing_rows(tmp_path: Path) -> None:
    image = _file(tmp_path, "red.png", "image/png", _png())
    gone = {
        "id": str(uuid.uuid4()),
        "filename": "gone.png",
        "mime_type": "image/png",
        "form": "image",
    }
    # Pinned as an image, but the agent's model no longer has vision.
    middleware = AttachmentInputMiddleware(AttachmentInputs({image.id: image}, False))

    (sent,) = _call(middleware, [_human("hi", [_ref(image, "image"), gone])])

    blocks = sent.content[1:]
    assert all(block["type"] == "text" for block in blocks)
    assert "이미지를 지원하지 않아" in blocks[0]["text"]
    assert "gone.png" in blocks[1]["text"] and "찾을 수 없어" in blocks[1]["text"]


def test_middleware_sends_only_the_latest_images(tmp_path: Path) -> None:
    images = [
        _file(tmp_path, f"{index}.png", "image/png", _png())
        for index in range(MAX_IMAGES_PER_CALL + 1)
    ]
    messages = [_human(f"turn {index}", [_ref(item, "image")]) for index, item in enumerate(images)]
    middleware = AttachmentInputMiddleware(
        AttachmentInputs({item.id: item for item in images}, True)
    )

    sent = _call(middleware, messages)

    kinds = [message.content[1]["type"] for message in sent]
    assert kinds == ["text"] + ["image"] * MAX_IMAGES_PER_CALL


def test_middleware_downscales_large_images_and_caps_text(tmp_path: Path) -> None:
    large = _png((1800, 1800), noise=True)
    assert len(large) > MAX_IMAGE_BYTES
    image = _file(tmp_path, "photo.png", "image/png", large)
    text = _file(tmp_path, "big.txt", "text/plain", b"a" * (MAX_TEXT_CHARS + 10))
    middleware = AttachmentInputMiddleware(AttachmentInputs({image.id: image, text.id: text}, True))

    (sent,) = _call(middleware, [_human("", [_ref(image, "image"), _ref(text, "text")])])

    image_block, text_block = sent.content
    data = base64.b64decode(image_block["base64"])
    assert image_block["mime_type"] == "image/jpeg"
    assert len(data) <= MAX_IMAGE_BYTES
    assert max(Image.open(io.BytesIO(data)).size) <= 1568
    assert f"앞부분 {MAX_TEXT_CHARS}자만" in text_block["text"]


def test_middleware_sends_pdf_text_not_the_file(tmp_path: Path) -> None:
    pdf = _file(tmp_path, "report.pdf", "application/pdf", _pdf("First page", "", "Third page"))
    # Vision does not matter: only the extracted text is sent.
    middleware = AttachmentInputMiddleware(AttachmentInputs({pdf.id: pdf}, None))

    (sent,) = _call(middleware, [_human("read", [_ref(pdf, "pdf")])])

    _, block = sent.content
    assert block["type"] == "text"
    assert "[첨부 파일: report.pdf]" in block["text"]
    assert "First page\nThird page" in block["text"]
    assert "앞부분" not in block["text"]


def test_middleware_caps_pdf_text(tmp_path: Path) -> None:
    line = "x" * 1000
    pages = [line] * (MAX_TEXT_CHARS // 1000 + 5)
    pdf = _file(tmp_path, "long.pdf", "application/pdf", _pdf(*pages))
    middleware = AttachmentInputMiddleware(AttachmentInputs({pdf.id: pdf}, True))

    (sent,) = _call(middleware, [_human("", [_ref(pdf, "pdf")])])

    (block,) = sent.content
    body = block["text"].split("```\n", 1)[1].rsplit("\n```", 1)[0]
    assert len(body) == MAX_TEXT_CHARS
    assert f"앞부분 {MAX_TEXT_CHARS}자만" in block["text"]


@pytest.mark.parametrize(
    ("data", "reason"),
    [
        (_pdf("", ""), "추출할 텍스트가 없습니다"),
        (_pdf("secret", password="pw"), "암호가 걸린 PDF"),
        (b"%PDF-1.4\nnot a pdf", "PDF를 읽지 못해"),
    ],
)
def test_middleware_notes_pdfs_without_text(tmp_path: Path, data: bytes, reason: str) -> None:
    pdf = _file(tmp_path, "scan.pdf", "application/pdf", data)
    middleware = AttachmentInputMiddleware(AttachmentInputs({pdf.id: pdf}, True))

    (sent,) = _call(middleware, [_human("", [_ref(pdf, "pdf")])])

    (block,) = sent.content
    assert block["type"] == "text"
    assert "scan.pdf" in block["text"] and reason in block["text"]
    assert "secret" not in block["text"]


def test_middleware_ignores_refs_on_non_human_messages(tmp_path: Path) -> None:
    image = _file(tmp_path, "red.png", "image/png", _png())
    middleware = AttachmentInputMiddleware(AttachmentInputs({image.id: image}, True))
    message = AIMessage(
        content="x", additional_kwargs={ATTACHMENT_REFS_KEY: [_ref(image, "image")]}
    )
    assert _call(middleware, [message]) == [message]


def test_middleware_passes_through_without_refs(tmp_path: Path) -> None:
    middleware = AttachmentInputMiddleware(AttachmentInputs({}, True))
    message = HumanMessage(content="plain")
    assert _call(middleware, [message]) == [message]


def test_attachment_input_is_a_lease_fallback() -> None:
    assert is_legacy_fallback("attachment_input")


async def _seed_run(conversation_id: uuid.UUID, *, supports_vision: bool | None) -> uuid.UUID:
    async with TestSession() as db:
        conv = await db.get(Conversation, conversation_id)
        assert conv is not None
        agent = await db.get(Agent, require_conversation_agent_id(conv))
        assert agent is not None
        model = await db.get(Model, agent.model_id)
        assert model is not None
        model.supports_vision = supports_vision
        run = await conversation_run_service.create_run(
            db,
            conversation_id=conversation_id,
            agent_id=agent.id,
            context=unit_context(TEST_USER_ID),
            source="chat",
            input_preview="with files",
        )
        await db.commit()
        return run.id


async def _model_id(conversation_id: uuid.UUID) -> str:
    async with TestSession() as db:
        conv = await db.get(Conversation, conversation_id)
        assert conv is not None
        agent = await db.get(Agent, require_conversation_agent_id(conv))
        assert agent is not None
        return str(agent.model_id)


async def _seed_attachment(
    conversation_id: uuid.UUID | None, name: str, mime: str, data: bytes
) -> uuid.UUID:
    upload_id = uuid.uuid4()
    base = Path(settings.upload_dir) / "tenants" / str(TEST_TENANT_ID)
    base.mkdir(parents=True, exist_ok=True)
    path = base / f"{upload_id}{Path(name).suffix}"
    path.write_bytes(data)
    async with TestSession() as db:
        db.add(
            MessageAttachment(
                id=upload_id,
                user_id=TEST_USER_ID,
                conversation_id=conversation_id,
                filename=name,
                mime_type=mime,
                size_bytes=len(data),
                storage_path=str(path),
                url=f"/api/uploads/{upload_id}",
                org_id=TEST_ORG_ID,
                tenant_id=TEST_TENANT_ID,
            )
        )
        await db.commit()
    return upload_id


@pytest.fixture
def upload_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    from app import database

    monkeypatch.setattr(settings, "upload_dir", str(tmp_path / "uploads"))
    # The runtime opens its own company session (``runtime_db_session_factory``).
    monkeypatch.setattr(database, "async_session", TestSession)
    return tmp_path / "uploads"


async def _prepare(
    run_id: uuid.UUID,
    conversation_id: uuid.UUID,
    attachment_ids: list[uuid.UUID] | None,
    payload: Any,
    *,
    tag_input: bool = True,
) -> tuple[Any, AttachmentInputs | None]:
    cfg = await run_fixture_config(run_id, TestSession)
    return await conversation_run_worker._prepare_turn_attachment_inputs(
        run_id,
        WorkerConversationTarget(conversation_id, cfg.org_context),
        await _model_id(conversation_id),
        attachment_ids,
        payload,
        tag_input=tag_input,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(("vision", "image_form"), [(True, "image"), (None, "image_no_vision")])
async def test_prepare_tags_input_and_records_the_run(
    upload_dir: Path, vision: bool | None, image_form: str
) -> None:
    conversation_id = await seed_conversation_with_agent()
    run_id = await _seed_run(conversation_id, supports_vision=vision)
    image_id = await _seed_attachment(conversation_id, "a.png", "image/png", _png())
    text_id = await _seed_attachment(conversation_id, "b.txt", "text/plain", b"hello")
    pdf_id = await _seed_attachment(conversation_id, "d.pdf", "application/pdf", _pdf("hi"))
    elsewhere = await _seed_attachment(
        await seed_conversation_with_agent(), "c.txt", "text/plain", b"x"
    )
    await _seed_attachment(None, "unsent.png", "image/png", _png())

    payload, inputs = await _prepare(
        run_id,
        conversation_id,
        [text_id, image_id, pdf_id, elsewhere],
        {"messages": [{"type": "human", "content": "see"}]},
    )

    refs = payload["messages"][0]["additional_kwargs"][ATTACHMENT_REFS_KEY]
    assert [(ref["id"], ref["form"]) for ref in refs] == [
        (str(text_id), "text"),
        (str(image_id), image_form),
        (str(pdf_id), "pdf"),
    ]
    assert inputs is not None
    # this conversation only
    assert set(inputs.files) == {str(text_id), str(image_id), str(pdf_id)}
    assert inputs.supports_vision is vision
    async with TestSession() as db:
        run = await db.get(ConversationRun, run_id)
        assert run is not None
        assert run.metadata_json is not None
        record = run.metadata_json["attachment_inputs"]
        assert await conversation_has_attachments(
            db, conversation_id=conversation_id, context=unit_context(TEST_USER_ID)
        )
    assert record["supports_vision"] is vision
    assert [item["form"] for item in record["items"]] == ["text", image_form, "pdf"]


@pytest.mark.asyncio
async def test_resume_reloads_files_without_tagging_or_recording(upload_dir: Path) -> None:
    conversation_id = await seed_conversation_with_agent()
    run_id = await _seed_run(conversation_id, supports_vision=True)
    image_id = await _seed_attachment(conversation_id, "a.png", "image/png", _png())
    resume = {"decisions": [{"type": "approve"}]}

    payload, inputs = await _prepare(run_id, conversation_id, None, resume, tag_input=False)

    assert payload is resume
    assert inputs is not None and set(inputs.files) == {str(image_id)}
    async with TestSession() as db:
        run = await db.get(ConversationRun, run_id)
        assert run is not None
    assert "attachment_inputs" not in (run.metadata_json or {})


@pytest.mark.asyncio
async def test_prepare_without_attachments_loads_nothing(upload_dir: Path) -> None:
    conversation_id = await seed_conversation_with_agent()
    run_id = await _seed_run(conversation_id, supports_vision=True)
    forged = {"type": "human", "content": "hi", ATTACHMENT_REFS_KEY: [{"id": "x"}]}

    payload, inputs = await _prepare(run_id, conversation_id, [], {"messages": [forged]})

    assert inputs is None
    assert ATTACHMENT_REFS_KEY not in payload["messages"][0]
    async with TestSession() as db:
        assert not await conversation_has_attachments(
            db, conversation_id=conversation_id, context=unit_context(TEST_USER_ID)
        )


def test_image_block_converts_for_each_provider(tmp_path: Path) -> None:
    """The standard block the middleware emits is what each provider SDK accepts."""
    from langchain_anthropic.chat_models import _format_messages
    from langchain_google_genai.chat_models import _parse_chat_history
    from langchain_openai.chat_models.base import _convert_message_to_dict

    image = _file(tmp_path, "red.png", "image/png", _png())
    middleware = AttachmentInputMiddleware(AttachmentInputs({image.id: image}, True))
    (sent,) = _call(middleware, [_human("look", [_ref(image, "image")])])
    encoded = sent.content[1]["base64"]

    openai_block = _convert_message_to_dict(sent)["content"][1]
    assert openai_block["image_url"]["url"] == f"data:image/png;base64,{encoded}"
    anthropic_block = _format_messages([sent], model="claude-sonnet-4-5")[1][0]["content"][1]
    assert anthropic_block["source"] == {
        "type": "base64",
        "media_type": "image/png",
        "data": encoded,
    }
    _, google_history = _parse_chat_history([sent])
    google_parts = google_history[0].parts
    assert google_parts is not None
    inline = google_parts[1].inline_data
    assert inline is not None and inline.data == image.path.read_bytes()


@pytest.mark.asyncio
async def test_deep_agent_graph_sends_the_image_but_checkpoints_the_reference(
    tmp_path: Path,
) -> None:
    from deepagents import create_deep_agent
    from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
    from langgraph.checkpoint.memory import MemorySaver

    seen: list[list[Any]] = []

    class CapturingModel(FakeMessagesListChatModel):
        def bind_tools(self, tools: Any, **kwargs: Any) -> CapturingModel:
            return self

        def _generate(self, messages: list[Any], *args: Any, **kwargs: Any) -> Any:
            seen.append(messages)
            return super()._generate(messages, *args, **kwargs)

    image = _file(tmp_path, "red.png", "image/png", _png())
    ref = _ref(image, "image")
    agent = create_deep_agent(
        model=CapturingModel(responses=[AIMessage(content="빨간 사각형입니다.")]),
        tools=[],
        system_prompt="Describe attachments.",
        middleware=[AttachmentInputMiddleware(AttachmentInputs({image.id: image}, True))],
        checkpointer=MemorySaver(),
    )
    config: RunnableConfig = {"configurable": {"thread_id": "attachment-graph"}}
    payload = tag_input_with_attachments(
        {"messages": [{"type": "human", "content": "이 이미지는?"}]}, [ref]
    )

    await agent.ainvoke(payload, config)

    human = next(message for message in seen[0] if message.type == "human")
    assert [block["type"] for block in human.content] == ["text", "image"]
    state = await agent.aget_state(config)
    stored = next(message for message in state.values["messages"] if message.type == "human")
    assert stored.content == "이 이미지는?"
    assert stored.additional_kwargs[ATTACHMENT_REFS_KEY] == [ref]


@pytest.mark.asyncio
@pytest.mark.parametrize("with_inputs", [True, False])
async def test_prepare_agent_installs_the_middleware_only_with_inputs(
    monkeypatch: pytest.MonkeyPatch, with_inputs: bool
) -> None:
    from types import SimpleNamespace

    from app.agent_runtime import runtime_component_builder as builder
    from app.agent_runtime.runtime_config import AgentConfig

    components = SimpleNamespace(
        model=object(),
        tools=[],
        system_prompt="p",
        middleware=["existing"],
        interrupt_on=None,
        backend=None,
        skills_sources=None,
        memory_sources=None,
        permissions=[],
    )

    async def fake_components(*_args: Any, **_kwargs: Any) -> Any:
        return components

    built: dict[str, Any] = {}

    def fake_build_agent(*_args: Any, **kwargs: Any) -> str:
        built.update(kwargs)
        return "agent"

    monkeypatch.setattr(builder, "_prepare_runtime_components", fake_components)
    monkeypatch.setattr(builder, "build_agent", fake_build_agent)
    monkeypatch.setattr("app.agent_runtime.checkpointer.get_checkpointer", lambda: None)
    inputs = AttachmentInputs({}, True) if with_inputs else None
    cfg = AgentConfig(
        org_context=unit_context(TEST_USER_ID),
        provider="openai",
        model_name="m",
        api_key=None,
        base_url=None,
        system_prompt="p",
        tools_config=[],
        thread_id=str(uuid.uuid4()),
        attachment_inputs=inputs,
    )

    await builder._prepare_agent(cfg, messages_history=[], run_id="run-1")

    middleware = built["middleware"]
    assert middleware[0] == "existing"
    assert components.middleware == ["existing"]
    if with_inputs:
        assert isinstance(middleware[-1], AttachmentInputMiddleware)
    else:
        assert len(middleware) == 1


@pytest.mark.asyncio
async def test_worker_runs_with_tagged_input_and_attachment_inputs(upload_dir: Path) -> None:
    from collections.abc import AsyncGenerator

    from app.dependencies import CurrentUser

    conversation_id = await seed_conversation_with_agent()
    run_id = await _seed_run(conversation_id, supports_vision=True)
    image_id = await _seed_attachment(conversation_id, "a.png", "image/png", _png())
    cfg = await run_fixture_config(run_id, TestSession)
    cfg.model_id = await _model_id(conversation_id)
    captured: dict[str, Any] = {}

    async def capture(run_cfg: Any, payload: Any, **_kwargs: Any) -> AsyncGenerator[str, None]:
        captured["cfg"], captured["payload"] = run_cfg, payload
        if False:  # pragma: no cover - async generator shape
            yield ""

    registry = conversation_run_worker.RunTaskRegistry(worker_instance_id="attachment-worker")
    await conversation_run_worker.start_conversation_run(
        run_id=run_id,
        conversation_id=conversation_id,
        cfg=cfg,
        user=CurrentUser(id=TEST_USER_ID, email="test@test.com", name="Test"),
        input_payload={"messages": [{"type": "human", "content": "see"}]},
        moldy_source="chat",
        executor_fn=capture,
        registry=registry,
        attachment_ids=[image_id],
    )
    task = registry.get(run_id)
    assert task is not None
    await task

    refs = captured["payload"]["messages"][0]["additional_kwargs"][ATTACHMENT_REFS_KEY]
    assert [ref["id"] for ref in refs] == [str(image_id)]
    assert set(captured["cfg"].attachment_inputs.files) == {str(image_id)}
