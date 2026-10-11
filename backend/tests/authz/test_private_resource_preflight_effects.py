"""Independent resource denial must precede unrelated private SQL writes."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Literal, assert_never
from unittest.mock import Mock
from uuid import uuid4

import pytest
from anyio import Path as AsyncPath
from sqlalchemy import event, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.agent import Agent
from app.models.message_attachment import MessageAttachment
from app.models.skill import AgentSkillLink, Skill
from app.schemas.chat_resource_context import ChatResourceContextRequest, ChatResourceReference
from app.services import chat_resource_context
from app.services.chat.conversations import create_conversation
from app.services.chat_resource_context import ChatResourceContextResolver
from app.services.chat_resource_context_errors import ResourceContextNotFoundError
from app.services.chat_resource_context_sources import ResourceContextScope

if TYPE_CHECKING:
    from tests.authz.private_scope_fixture import PrivateScopes
    from tests.authz.test_private_agent_api_scope import ApiCase

pytest_plugins = ["tests.authz.test_private_agent_api_scope"]
Kind = Literal["file", "skill"]


def test_production_provenance() -> None:
    expected = Path(__file__).resolve().parents[2]
    assert Path(chat_resource_context.__file__).resolve().is_relative_to(expected)


@pytest.mark.parametrize("kind", ["file", "skill"])
async def test_foreign_resource_denied_without_private_sql_effect(
    db: AsyncSession,
    private_scopes: PrivateScopes,
    api_case: ApiCase,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    kind: Kind,
) -> None:
    own = private_scopes.first
    conversation = await create_conversation(db, api_case.first.agent_id, context=own)
    foreign_conversation = await create_conversation(
        db, api_case.second.agent_id, context=private_scopes.second
    )
    references: list[ChatResourceReference] = []
    for context, parent in ((own, conversation), (private_scopes.second, foreign_conversation)):
        path = tmp_path / (kind + "-" + context.org_id.hex + ".txt")
        await AsyncPath(path).write_text("F1_R17_DUMMY_RESOURCE", encoding="utf-8")
        match kind:
            case "file":
                resource = MessageAttachment(
                    id=uuid4(),
                    user_id=context.user_id,
                    org_id=context.org_id,
                    tenant_id=context.tenant_id,
                    conversation_id=parent.id,
                    filename=path.name,
                    mime_type="text/plain",
                    size_bytes=20,
                    storage_path=str(path),
                    url="/api/uploads/fixture",
                )
                db.add(resource)
                references.append(ChatResourceReference(kind="file", id=resource.id))
            case "skill":
                resource = Skill(
                    id=uuid4(),
                    user_id=context.user_id,
                    org_id=context.org_id,
                    tenant_id=context.tenant_id,
                    name="Fixture",
                    slug=uuid4().hex,
                    kind="text",
                    storage_path=str(path),
                    content_hash="a" * 64,
                )
                db.add(resource)
                await db.flush()
                db.add(AgentSkillLink(agent_id=api_case.first.agent_id, skill_id=resource.id))
                references.append(ChatResourceReference(kind="skill", id=resource.id))
            case unreachable:
                assert_never(unreachable)
    await db.commit()
    resolver = ChatResourceContextResolver(
        db, ResourceContextScope(own, api_case.first.agent_id, conversation.id)
    )
    control = await resolver.resolve(ChatResourceContextRequest(resources=[references[0]]))
    assert control.resources[0].text == "F1_R17_DUMMY_RESOURCE"
    source_agent = await db.get(Agent, api_case.first.agent_id)
    assert source_agent is not None
    staged = Agent(
        id=uuid4(),
        user_id=own.user_id,
        org_id=own.org_id,
        tenant_id=own.tenant_id,
        name="Pending denial witness",
        system_prompt="F1_R17_DUMMY_PENDING",
        model_id=source_agent.model_id,
        runtime_name="f1-r17-" + uuid4().hex,
    )
    db.add(staged)
    flush = Mock()
    reads: list[Path] = []
    original_read = chat_resource_context._read_prefix

    def observed_read(path: Path, byte_count: int) -> bytes:
        reads.append(path)
        return original_read(path, byte_count)

    monkeypatch.setattr(chat_resource_context, "_read_prefix", observed_read)
    event.listen(db.sync_session, "before_flush", flush)
    try:
        with pytest.raises(ResourceContextNotFoundError):
            await resolver.resolve(ChatResourceContextRequest(resources=[references[1]]))
    finally:
        event.remove(db.sync_session, "before_flush", flush)
    with db.no_autoflush:
        stored = await db.scalar(
            select(func.count()).select_from(Agent).where(Agent.id == staged.id)
        )
    await db.rollback()
    assert not reads
    assert stored == 0 and flush.call_count == 0, (
        "Private Agent INSERT preceded foreign resource denial"
    )
