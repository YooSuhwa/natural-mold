"""Private file-list projections have bounded SQL work and recheck every saved caption."""

from __future__ import annotations

import asyncio
import hashlib
import io
import json
import threading
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock
from uuid import UUID, uuid4

import pytest
from langchain_core.messages import HumanMessage
from PIL import Image
from sqlalchemy import event, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.exceptions import AppError
from app.models.chat_image_description import ChatImageDescription
from app.models.conversation import Conversation
from app.models.conversation_run import ConversationRun
from app.models.message_attachment import MessageAttachment
from app.models.organization import OrganizationMember, TenantMember
from app.schemas.conversation import FileItem
from app.services.chat import image_description_provider as provider
from app.services.chat.attachments import list_conversation_files
from app.services.chat.image_description_input import question_digest
from app.services.chat.messages import list_messages_from_checkpointer
from app.services.thread_branch_service import MessageTree, MessageTreeNode
from tests.authz.f05_support import enforce
from tests.conftest import TestSession, make_user
from tests.sqlite_test_engine import engine

if TYPE_CHECKING:
    from app.authz.org_context import OrgContext
    from tests.authz.image_description_fixture import Case
    from tests.authz.private_scope_fixture import PrivateScopes

pytest_plugins = ["tests.authz.image_description_fixture"]

_QUESTION = "private test question"
_CAPTION = "Scoped caption"
_FIXTURE_KEY = "unused-test-ciphertext"


def _write_sample(path: Path, number: int) -> tuple[int, str]:
    path.parent.mkdir(parents=True, exist_ok=True)
    image = io.BytesIO()
    Image.new("RGB", (3, 3), (number, 42, 80)).save(image, format="PNG")
    raw = image.getvalue()
    path.write_bytes(raw)
    return len(raw), hashlib.sha256(raw).hexdigest()


async def _seed(
    db: AsyncSession, case: Case, path: Path, indices: range
) -> dict[str, tuple[Path, str]]:
    policy = (await case.resolve(db)).policy_fingerprint
    images = {}
    for number in indices:
        attachment_id, run_id, message_id = uuid4(), uuid4(), str(uuid4())
        file_path = path / "tenants" / str(case.context.tenant_id) / f"{attachment_id}.png"
        size, digest = await asyncio.to_thread(_write_sample, file_path, number)
        q_hash = question_digest(f"{_QUESTION} {number}")
        caption = f"{_CAPTION} {number}"
        ref = {
            "form": "image_description",
            "id": str(attachment_id),
            "description_run": str(run_id),
            "question_sha256": q_hash,
            "content_sha256": digest,
            "description_policy": policy,
            "description_model": str(case.model.id),
        }
        shared = {
            "tenant_id": case.context.tenant_id,
            "org_id": case.context.org_id,
            "conversation_id": case.run.conversation_id,
            "user_id": case.context.user_id,
        }
        db.add(
            MessageAttachment(
                **shared,
                id=attachment_id,
                message_id=message_id,
                filename=f"sample-{number}.png",
                mime_type="image/png",
                size_bytes=size,
                storage_path=str(file_path),
                content_sha256=digest,
                url=f"/api/uploads/{attachment_id}",
            )
        )
        db.add(
            ConversationRun(
                **shared,
                id=run_id,
                credential_subject_user_id=case.context.user_id,
                resource_owner_user_id=case.context.user_id,
                agent_id=case.run.agent_id,
                status="completed",
                is_active=False,
                run_source="CHAT",
                source="chat",
                invoker_type="user",
                metadata_json={
                    "attachment_inputs": {
                        "supports_vision": False,
                        "message_id": message_id,
                        "items": [ref],
                    }
                },
            )
        )
        await db.flush()
        db.add(
            ChatImageDescription(
                run_id=run_id,
                attachment_id=attachment_id,
                conversation_id=case.run.conversation_id,
                model_id=case.model.id,
                question_sha256=q_hash,
                content_sha256=digest,
                policy_sha256=hashlib.sha256(policy.encode()).hexdigest(),
                status="completed",
                description=caption,
            )
        )
        images[message_id] = (file_path, caption)
    await db.commit()
    return images


async def _list(case: Case, ctx: OrgContext | None = None) -> tuple[list[FileItem], int]:
    count = 0

    def observe(*_args: object) -> None:
        nonlocal count
        count += 1

    actor = ctx or case.context
    async with TestSession() as session:
        event.listen(engine.sync_engine, "before_cursor_execute", observe)
        try:
            files = await list_conversation_files(
                session,
                context=actor,
                conversation_id=case.run.conversation_id,
                user_id=actor.user_id,
            )
        finally:
            event.remove(engine.sync_engine, "before_cursor_execute", observe)
    return files, count


def _captions(files: list[FileItem]) -> dict[str, str]:
    return {
        use.message_id: use.image_description.text
        for file in files
        for use in file.image_description_uses
        if use.image_description.status == "ready"
    }


@pytest.fixture
def private_uploads(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setattr(settings, "upload_dir", str(tmp_path))
    for name in ("send_image_description", "open_values"):
        monkeypatch.setattr(provider, name, AsyncMock(side_effect=AssertionError(name)))
    return tmp_path


async def test_many_distinct_runs_and_uploads_share_read_queries(
    db: AsyncSession,
    case: Case,
    private_uploads: Path,
    record_property: Callable[[str, int], None],
) -> None:
    expected = await _seed(db, case, private_uploads, range(1))
    first, one_count = await _list(case)
    assert _captions(first) == {mid: item[1] for mid, item in expected.items()}

    expected.update(await _seed(db, case, private_uploads, range(1, 35)))
    all_files, many_count = await _list(case)
    assert len(all_files) == 35
    assert _captions(all_files) == {mid: item[1] for mid, item in expected.items()}
    assert all(
        file.image_description == file.image_description_uses[0].image_description
        for file in all_files
    )
    serialized = json.dumps(
        [file.model_dump(mode="json") for file in all_files], ensure_ascii=False
    )
    assert _QUESTION not in serialized and _FIXTURE_KEY not in serialized
    record_property("sql_queries_one_image", one_count)
    record_property("sql_queries_35_images", many_count)
    assert many_count <= one_count + 3, f"one use: {one_count}, 35 uses: {many_count}"


async def test_missing_and_modified_uploads_hide_only_their_captions(
    db: AsyncSession,
    case: Case,
    private_uploads: Path,
) -> None:
    expected = await _seed(db, case, private_uploads, range(3))
    files, _ = await _list(case)
    assert len(_captions(files)) == 3
    (deleted, _), (changed, _), (_, good_caption) = expected.values()
    await asyncio.to_thread(deleted.unlink)
    await asyncio.to_thread(_write_sample, changed, 251)
    files, _ = await _list(case)
    assert list(_captions(files).values()) == [good_caption]


@pytest.mark.parametrize("withdraw", ["policy", "credential", "grant", "epoch"])
async def test_read_rechecks_policy_and_grant_after_previous_projection(
    db: AsyncSession,
    case: Case,
    private_uploads: Path,
    monkeypatch: pytest.MonkeyPatch,
    withdraw: str,
) -> None:
    actor = f"user:{case.context.user_id}"
    model_grant = (actor, "can_use", f"llm_model:{case.model.id}")
    key_grant = (actor, "can_use", f"credential:{case.credential.id}")
    monkeypatch.setattr(provider, "runtime_authz", lambda: enforce(model_grant, key_grant))
    await _seed(db, case, private_uploads, range(2))
    initial, _ = await _list(case)
    assert len(_captions(initial)) == 2
    if withdraw == "policy":
        case.org.settings = {}
    elif withdraw == "credential":
        case.model.default_credential_id = None
    elif withdraw == "epoch":
        case.org.authz_epoch += 1
    else:
        monkeypatch.setattr(provider, "runtime_authz", lambda: enforce(key_grant))
    await db.commit()
    after, _ = await _list(case)
    assert len(after) == 2 and _CAPTION not in repr(after)


async def test_warm_owner_results_are_private_to_their_actor_and_org(
    db: AsyncSession, case: Case, private_uploads: Path, private_scopes: PrivateScopes
) -> None:
    await _seed(db, case, private_uploads, range(1))
    first, _ = await _list(case)
    assert len(_captions(first)) == 1
    outsider = await make_user(db)
    member = replace(case.context, user_id=outsider.id)
    db.add(TenantMember(tenant_id=member.tenant_id, user_id=member.user_id))
    db.add(
        OrganizationMember(org_id=member.org_id, tenant_id=member.tenant_id, user_id=member.user_id)
    )
    await db.commit()
    for other in (member, private_scopes.second):
        with pytest.raises(AppError) as denied:
            await _list(case, other)
        assert denied.value.status == 404


async def test_anonymous_history_does_not_receive_private_file_descriptions(
    db: AsyncSession, case: Case, private_uploads: Path
) -> None:
    expected = await _seed(db, case, private_uploads, range(1))
    message_id = next(iter(expected))
    files, _ = await _list(case)
    assert message_id in _captions(files)
    conversation = await db.get(Conversation, case.run.conversation_id)
    assert conversation is not None
    tree = MessageTree(
        nodes=[MessageTreeNode(HumanMessage(content="Show this chart", id=message_id), None, "cp")],
        active_tip_message_id=message_id,
        active_checkpoint_id="cp",
    )
    (public_message,) = await list_messages_from_checkpointer(
        db,
        conversation,
        user_id=None,
        context=case.context,
        tree=tree,
    )
    assert str(public_message.id) == message_id
    assert not public_message.attachments
    assert _CAPTION not in public_message.model_dump_json()


@pytest.mark.parametrize("change", ["epoch", "unlink"])
async def test_revocations_during_file_integrity_scan_discard_changed_captions(
    db: AsyncSession,
    case: Case,
    private_uploads: Path,
    monkeypatch: pytest.MonkeyPatch,
    change: str,
) -> None:
    from app.services.chat import image_description_files as projection

    expected = await _seed(db, case, private_uploads, range(2))
    first_message = next(iter(expected))
    original = projection._verify_versions
    started = asyncio.Event()
    resume = threading.Event()
    loop = asyncio.get_running_loop()

    def scan(
        files: dict[tuple[UUID, str], tuple[str, int]], tenant_id: UUID
    ) -> set[tuple[UUID, str]]:
        loop.call_soon_threadsafe(started.set)
        if not resume.wait(timeout=10):
            raise TimeoutError("file scan did not resume")
        return original(files, tenant_id)

    monkeypatch.setattr(projection, "_verify_versions", scan)
    reader = asyncio.create_task(_list(case))
    try:
        await asyncio.wait_for(started.wait(), timeout=10)
        if change == "epoch":
            case.org.authz_epoch += 1
        else:
            await db.execute(
                update(MessageAttachment)
                .where(MessageAttachment.message_id == first_message)
                .values(conversation_id=None)
            )
        await db.commit()
    finally:
        resume.set()
        files, _ = await reader
    if change == "epoch":
        assert _CAPTION not in repr(files)
    else:
        assert expected[first_message][1] not in repr(files)
        assert set(_captions(files)) == set(expected) - {first_message}
