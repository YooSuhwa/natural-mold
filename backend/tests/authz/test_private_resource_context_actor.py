"""Actual file/artifact source reads must refresh captured private authority."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Literal, assert_never

import pytest
from anyio import Path as AsyncPath
from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from app.authz.private_actor import PrivateResourceActor, resolve_private_actor
from app.exceptions import AppError
from app.models.conversation_artifact import ArtifactVersion, ConversationArtifact
from app.models.message_attachment import MessageAttachment
from app.models.organization import OrganizationMember
from app.schemas.chat_resource_context import ChatResourceContextRequest, ChatResourceReference
from app.services import chat_resource_context
from app.services.artifact_storage import LocalArtifactStorageBackend
from app.services.chat_resource_context import (
    ChatResourceContextResolver,
    ResourceContextDependencies,
)
from app.services.chat_resource_context_errors import ResourceContextNotFoundError
from app.services.chat_resource_context_sources import ResourceContextScope

if TYPE_CHECKING:
    from tests.authz.private_scope_fixture import PrivateScopes
    from tests.authz.test_private_secret_collector import CollectorCase

pytest_plugins = [
    "tests.authz.test_private_attachment_scope",
    "tests.authz.test_private_artifact_scope",
]
Kind = Literal["file", "artifact"]


@pytest.mark.parametrize("kind", ["file", "artifact"])
@pytest.mark.parametrize("revoked", [False, True])
async def test_resource_hydration_refreshes_actor_before_filesystem(
    db: AsyncSession,
    private_scopes: PrivateScopes,
    collector_case: CollectorCase,
    private_upload: MessageAttachment,
    private_artifact: ConversationArtifact,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    kind: Kind,
    revoked: bool,
) -> None:
    stored = tmp_path / "owned-private.txt"
    await AsyncPath(stored).write_text("F4_R11_PRIVATE_RESOURCE_BODY", encoding="utf-8")
    own = private_scopes.first
    conversation = collector_case.conversation
    assert conversation.agent_id is not None
    match kind:
        case "file":
            private_upload.conversation_id = conversation.id
            private_upload.storage_path = str(stored)
            reference = ChatResourceReference(kind="file", id=private_upload.id)
        case "artifact":
            version = ArtifactVersion(
                artifact_id=private_artifact.id,
                version_number=1,
                object_key=stored.name,
                original_filename=stored.name,
                size_bytes=27,
                sha256="a" * 64,
            )
            db.add(version)
            await db.flush()
            private_artifact.current_version_id = version.id
            reference = ChatResourceReference(kind="artifact", id=private_artifact.id)
        case unreachable:
            assert_never(unreachable)
    await db.commit()
    resolver = ChatResourceContextResolver(
        db,
        ResourceContextScope(own, conversation.agent_id, conversation.id),
        ResourceContextDependencies(LocalArtifactStorageBackend(tmp_path)),
    )
    if revoked:
        await db.execute(
            update(OrganizationMember)
            .where(
                OrganizationMember.org_id == own.org_id,
                OrganizationMember.user_id == own.user_id,
            )
            .values(status="suspended")
        )
        await db.commit()
        with pytest.raises(AppError) as fresh_denial:
            await resolve_private_actor(
                db, PrivateResourceActor(own.user_id, own.org_id, own.tenant_id)
            )
        assert fresh_denial.value.status == 403
        print("FRESH_ACTOR_ORACLE status=403 membership=suspended")
    read_count = 0
    original_read = chat_resource_context._read_prefix

    def observed_read(path: Path, byte_count: int) -> bytes:
        nonlocal read_count
        read_count += 1
        return original_read(path, byte_count)

    monkeypatch.setattr(chat_resource_context, "_read_prefix", observed_read)
    denied = False
    returned_body = False
    try:
        result = await resolver.resolve(ChatResourceContextRequest(resources=[reference]))
        returned_body = result.resources[0].text == "F4_R11_PRIVATE_RESOURCE_BODY"
    except (AppError, ResourceContextNotFoundError):
        denied = True
    print(
        f"OBSERVED kind={kind} revoked={revoked} denied={denied} "
        f"filesystem_reads={read_count} returned_private_body={returned_body}"
    )
    assert (
        (denied and read_count == 0 and not returned_body)
        if revoked
        else (not denied and read_count == 1 and returned_body)
    )
