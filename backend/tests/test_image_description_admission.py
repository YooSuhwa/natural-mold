"""Image egress eligibility is fixed when a chat command is accepted."""

from __future__ import annotations

import io
from collections.abc import Callable, Coroutine
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock
from uuid import UUID, uuid4

import pytest
from httpx import AsyncClient
from PIL import Image
from sqlalchemy.ext.asyncio import AsyncSession

from app import database
from app.agent_runtime.attachment_inputs import ATTACHMENT_REFS_KEY, AttachmentInputMiddleware
from app.auth.cookies import ACTIVE_ORG_COOKIE
from app.config import settings
from app.models.agent import Agent
from app.models.organization import OrganizationRoleGrant
from app.services import conversation_run_queue_service, conversation_run_queue_worker
from app.services.chat import image_description_provider as provider
from app.services.chat.attachment_inputs import prepare_attachment_inputs
from app.services.chat.attachments import link_attachments_to_message
from app.services.chat.image_description_admission import (
    AcceptedImageDescription,
    accepted_image_description,
)
from tests.conftest import TestSession

if TYPE_CHECKING:
    from tests.authz.image_description_fixture import Case
    from tests.authz.org_context_fixture import OrgFixture

QUESTION = "이 이미지의 색을 설명해 주세요."
pytest_plugins = ["tests.authz.image_description_fixture"]


@dataclass(frozen=True)
class UploadCommand:
    attachment_id: UUID
    digest: str
    root_model_id: str
    send: Callable[[str], Coroutine[Any, Any, dict[str, Any]]]


@pytest.fixture
async def command(
    case: Case,
    org_fixture: OrgFixture,
    raw_client: AsyncClient,
    db: AsyncSession,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> UploadCommand:
    # Use a real authenticated upload so acceptance receives the server's content digest.
    raw_client.cookies.set(ACTIVE_ORG_COOKIE, str(case.context.org_id))
    monkeypatch.setattr(settings, "upload_dir", str(tmp_path))
    monkeypatch.setattr(database, "async_session", TestSession)
    png = io.BytesIO()
    Image.new("RGB", (8, 8), "blue").save(png, format="PNG")
    data = png.getvalue()
    upload = await raw_client.post(
        "/api/uploads",
        files={"file": ("sample.png", data, "image/png")},
        headers=org_fixture.session.headers(),
    )
    assert upload.status_code == 201, upload.text
    attachment_id = UUID(upload.json()["id"])
    case.run.status = "completed"
    case.run.is_active = False
    agent = await db.get(Agent, case.run.agent_id)
    assert agent is not None
    await db.commit()

    async def send(strategy: str) -> dict[str, Any]:
        cid = case.run.conversation_id
        result = await raw_client.post(
            f"/api/conversations/{cid}/langgraph/threads/{cid}/commands",
            headers=org_fixture.session.headers(),
            json={
                "id": "admission-check",
                "method": "run.start",
                "params": {
                    "clientRequestId": "same-upload-command",
                    "multitaskStrategy": strategy,
                    "input": {
                        "messages": [{"role": "user", "content": QUESTION}],
                        "attachments": [{"id": str(attachment_id)}],
                    },
                },
            },
        )
        assert result.status_code == 200, result.text
        return result.json()["result"]

    return UploadCommand(attachment_id, sha256(data).hexdigest(), str(agent.model_id), send)


async def test_direct_command_commits_image_pin_before_worker_start(
    case: Case,
    command: UploadCommand,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: list[UUID] = []

    async def inspect_start(*, run_id: UUID, **_kwargs: Any) -> None:
        async with TestSession() as worker_db:
            pin = await accepted_image_description(
                worker_db,
                context=case.context,
                conversation_id=case.run.conversation_id,
                run_id=run_id,
                root_model_id=command.root_model_id,
                attachment_id=command.attachment_id,
            )
            assert isinstance(pin, AcceptedImageDescription)
            assert pin.content_sha256 == command.digest
            assert pin.model_id == case.model.id
            assert pin.policy_fingerprint
            seen.append(run_id)

    monkeypatch.setattr(
        "app.routers.conversation_agent_protocol.start_conversation_run", inspect_start
    )
    direct = await command.send("reject")
    assert seen == [UUID(direct["run_id"])]


@pytest.mark.parametrize("policy_change", ["later-opt-in", "edited", "restored"])
async def test_queue_replay_and_claim_preserve_original_image_admission(
    case: Case,
    db: AsyncSession,
    command: UploadCommand,
    monkeypatch: pytest.MonkeyPatch,
    policy_change: str,
    raw_client: AsyncClient,
    org_fixture: OrgFixture,
) -> None:
    monkeypatch.setattr(
        conversation_run_queue_worker,
        "dispatch_next_for_conversation",
        AsyncMock(return_value=None),
    )
    db.add(
        OrganizationRoleGrant(
            tenant_id=case.context.tenant_id,
            org_id=case.org.id,
            role="admin",
            principal_type="user",
            principal_id=case.context.user_id,
        )
    )
    if policy_change == "later-opt-in":
        case.org.settings = {}
    await db.commit()
    expected_fingerprint = (
        None if policy_change == "later-opt-in" else (await case.resolve(db)).policy_fingerprint
    )
    accepted = await command.send("enqueue")
    assert accepted.get("run_id") is None

    path = f"/api/orgs/{case.org.id}"
    current = await raw_client.get(path)
    assert current.status_code == 200
    new_policy = {**case.policy_settings(), "max_output_tokens": 64}
    patches = [None, case.policy_settings()] if policy_change == "restored" else [new_policy]
    for policy in patches:
        current = await raw_client.patch(
            path,
            headers={**org_fixture.session.headers(), "If-Match": current.headers["etag"]},
            json={"settings": {"image_description": policy}},
        )
        assert current.status_code == 200, current.text
    retry = await command.send("enqueue")
    assert retry["input_id"] == accepted["input_id"]

    claim = await conversation_run_queue_service.claim_next_input(
        db, conversation_id=case.run.conversation_id, context=case.context
    )
    assert claim is not None and str(claim.input.id) == accepted["input_id"]
    await db.commit()
    pin = await accepted_image_description(
        db,
        context=case.context,
        conversation_id=case.run.conversation_id,
        run_id=claim.run.id,
        root_model_id=command.root_model_id,
        attachment_id=command.attachment_id,
    )
    if expected_fingerprint is not None:
        assert isinstance(pin, AcceptedImageDescription)
        assert pin.policy_fingerprint == expected_fingerprint
        assert pin.content_sha256 == command.digest
    else:
        assert pin is None

    # A policy enabled or changed after acceptance must not send even the original bytes.
    claim.run.status = "running"
    await db.commit()
    wire = AsyncMock(return_value=("private vision result", 2, 3))
    monkeypatch.setattr(provider, "open_values", AsyncMock(return_value={"api_key": "fixture"}))
    monkeypatch.setattr(provider, "send_image_description", wire)
    payload, inputs = await prepare_attachment_inputs(
        db,
        context=case.context,
        conversation_id=case.run.conversation_id,
        run_id=claim.run.id,
        model_id=command.root_model_id,
        attachment_ids=[command.attachment_id],
        input_payload={"messages": [{"role": "user", "content": QUESTION}]},
        tag_input=True,
    )
    assert inputs is not None
    ref = payload["messages"][0]["additional_kwargs"][ATTACHMENT_REFS_KEY][0]
    from tests.authz.test_image_description_chat import _send

    rendered = await _send(AttachmentInputMiddleware(inputs), ref, QUESTION)
    assert "private vision result" not in rendered
    wire.assert_not_awaited()


@pytest.mark.parametrize(
    ("denial", "provider_code", "public_reason"),
    [
        ("oversize", "size_limit", "size_limit"),
        ("no-input-rate", "budget_limit", "budget"),
        ("non-vision-model", "model_unavailable", "unavailable"),
    ],
)
async def test_admission_denial_is_visible_after_reconnect_without_sending_image(
    case: Case,
    db: AsyncSession,
    command: UploadCommand,
    raw_client: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
    denial: str,
    provider_code: str,
    public_reason: str,
) -> None:
    from tests.authz.test_image_description_chat import _send

    if denial == "oversize":
        case.org.settings = {"image_description": {**case.policy_settings(), "max_input_bytes": 1}}
    elif denial == "no-input-rate":
        case.model.cost_per_input_token = None
    else:
        case.model.supports_vision = False
    await db.commit()
    monkeypatch.setattr(
        "app.routers.conversation_agent_protocol.start_conversation_run", AsyncMock()
    )
    wire = AsyncMock(return_value=("should never be dispatched", 1, 1))
    secrets = AsyncMock(return_value={"api_key": "fixture"})
    monkeypatch.setattr(provider, "send_image_description", wire)
    monkeypatch.setattr(provider, "open_values", secrets)
    run_id = UUID((await command.send("reject"))["run_id"])
    conversation_id = case.run.conversation_id
    with pytest.raises(provider.ImageDescriptionDenied) as denied:
        await provider.resolve_image_description_target(
            db,
            context=case.context,
            conversation_id=conversation_id,
            run_id=run_id,
            attachment_id=command.attachment_id,
        )
    assert denied.value.code == provider_code

    message_id = str(uuid4())
    payload, inputs = await prepare_attachment_inputs(
        db,
        context=case.context,
        conversation_id=conversation_id,
        run_id=run_id,
        model_id=command.root_model_id,
        attachment_ids=[command.attachment_id],
        input_payload={"messages": [{"id": message_id, "role": "user", "content": QUESTION}]},
        tag_input=True,
    )
    assert inputs is not None
    ref = payload["messages"][0]["additional_kwargs"][ATTACHMENT_REFS_KEY][0]
    assert (ref["form"], ref.get("description_error")) == ("image_description", public_reason)
    rendered = await _send(AttachmentInputMiddleware(inputs), ref, QUESTION)
    assert "should never be dispatched" not in rendered
    bound = await link_attachments_to_message(
        db,
        conversation_id=conversation_id,
        context=case.context,
        attachment_ids=[command.attachment_id],
        message_id=message_id,
    )
    assert bound == 1
    await db.commit()

    files = await raw_client.get(f"/api/conversations/{conversation_id}/files")
    assert files.status_code == 200, files.text
    file = next(item for item in files.json() if item["id"] == str(command.attachment_id))
    outcome = {"status": "unavailable", "reason": public_reason}
    assert file["image_description"] == outcome
    assert file["image_description_uses"][0]["image_description"] == outcome
    wire.assert_not_awaited()
    secrets.assert_not_awaited()
