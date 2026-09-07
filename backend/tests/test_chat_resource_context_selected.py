from __future__ import annotations

import hashlib
import uuid
from pathlib import Path

import pytest
from langchain_core.messages import AIMessage, HumanMessage
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.conversation import Conversation
from app.models.skill import AgentSkillLink, Skill
from app.schemas.chat_resource_context import ChatResourceContextRequest, ChatResourceReference
from app.services.chat_resource_context_errors import ResourceContextNotFoundError
from app.services.thread_branch_service import MessageTree, MessageTreeNode
from tests.test_chat_resource_context_support import (
    resource_context_resolver,
    seed_resource_context,
)


@pytest.mark.asyncio
async def test_resolve_skill_requires_current_agent_selection(
    db: AsyncSession, tmp_path: Path
) -> None:
    seeded = await seed_resource_context(db)
    path = tmp_path / "SKILL.md"
    path.write_text("selected skill body", encoding="utf-8")
    skill = Skill(
        user_id=seeded.user.id,
        name="Selected",
        slug="selected",
        kind="text",
        storage_path=str(path),
        content_hash=hashlib.sha256(path.read_bytes()).hexdigest(),
        size_bytes=path.stat().st_size,
    )
    db.add(skill)
    await db.flush()
    link = AgentSkillLink(agent_id=seeded.agent.id, skill_id=skill.id)
    db.add(link)
    await db.flush()

    resolver = resource_context_resolver(db, seeded, tmp_path)
    request = ChatResourceContextRequest(
        resources=[ChatResourceReference(kind="skill", id=skill.id)]
    )
    frozen = await resolver.resolve(request)

    assert frozen.resources[0].text == "selected skill body"
    assert frozen.resources[0].source_version.startswith("sha256:")

    path.write_text("dirty replacement body", encoding="utf-8")
    skill.is_dirty = True
    authorized = await resolver.reauthorize(frozen)
    assert authorized.resources[0].text == "selected skill body"

    await db.delete(link)
    await db.flush()
    with pytest.raises(ResourceContextNotFoundError):
        await resolver.reauthorize(frozen)


@pytest.mark.asyncio
async def test_resolve_conversation_uses_owned_bounded_active_transcript(
    db: AsyncSession, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    seeded = await seed_resource_context(db)
    referenced = Conversation(agent_id=seeded.agent.id, title="Referenced")
    db.add(referenced)
    await db.flush()
    tree = MessageTree(
        nodes=[
            MessageTreeNode(
                message=HumanMessage(content="question", id=str(uuid.uuid4())),
                parent_id=None,
                introduced_by_checkpoint_id="ck-1",
            ),
            MessageTreeNode(
                message=AIMessage(content="answer", id=str(uuid.uuid4())),
                parent_id=None,
                introduced_by_checkpoint_id="ck-2",
            ),
        ],
        active_tip_message_id=None,
        active_checkpoint_id="ck-2",
    )

    async def fake_build_message_tree(
        _checkpointer: AsyncPostgresSaver,
        _thread_id: str,
        active_checkpoint_id: str | None = None,
    ) -> MessageTree:
        assert active_checkpoint_id == seeded.conversation.active_branch_checkpoint_id
        return tree

    monkeypatch.setattr(
        "app.services.thread_branch_service.build_message_tree", fake_build_message_tree
    )
    monkeypatch.setattr("app.agent_runtime.checkpointer.get_checkpointer", lambda: object())

    frozen = await resource_context_resolver(db, seeded, tmp_path).resolve(
        ChatResourceContextRequest(
            resources=[ChatResourceReference(kind="conversation", id=referenced.id)]
        )
    )

    assert "question" in frozen.resources[0].text
    assert "answer" in frozen.resources[0].text
    assert frozen.resources[0].source_version == "checkpoint:ck-2"
