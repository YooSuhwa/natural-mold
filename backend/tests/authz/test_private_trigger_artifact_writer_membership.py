"""Captured membership must still authorize trigger and artifact metadata writes."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal, assert_never
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.authz.org_context import OrgContext
from app.exceptions import AppError
from app.models.agent import Agent
from app.models.agent_trigger import AgentTrigger
from app.models.conversation_artifact import ConversationArtifact
from app.models.model import Model
from app.models.organization import OrganizationMember
from app.schemas.agent import AgentCreate
from app.schemas.trigger import TriggerCreate, TriggerUpdate
from app.services import agent_service, trigger_service
from app.services.artifacts import library
from app.services.chat.conversations import create_conversation

if TYPE_CHECKING:
    from tests.authz.private_scope_fixture import PrivateScopes
pytest_plugins = ["tests.authz.private_scope_fixture"]


@dataclass(frozen=True, slots=True)
class WriterTargets:
    agent: Agent
    trigger: AgentTrigger
    artifact: ConversationArtifact


async def _targets(db: AsyncSession, context: OrgContext) -> WriterTargets:
    model = Model(provider="openai", model_name="fixture", display_name="Fixture")
    db.add(model)
    await db.commit()
    agent = await agent_service.create_agent(
        db,
        AgentCreate(
            name="Writer fixture", system_prompt="Fixture", model_id=model.id, identity_mode="fixed"
        ),
        context,
    )
    trigger = await trigger_service.create_trigger(
        db,
        agent.id,
        context,
        TriggerCreate(
            trigger_type="interval",
            schedule_config={"interval_minutes": 10},
            input_message="Fixture",
        ),
    )
    conversation = await create_conversation(db, agent.id, context=context)
    artifact = ConversationArtifact(
        user_id=context.user_id,
        org_id=context.org_id,
        tenant_id=context.tenant_id,
        agent_id=agent.id,
        conversation_id=conversation.id,
        assistant_msg_id="fixture-turn",
        logical_path="fixture.txt",
        display_name="fixture.txt",
        mime_type="text/plain",
        size_bytes=7,
        sha256="a" * 64,
    )
    db.add(artifact)
    await db.commit()
    return WriterTargets(agent, trigger, artifact)


async def _suspend(db: AsyncSession, context: OrgContext) -> None:
    await db.execute(
        update(OrganizationMember)
        .where(
            OrganizationMember.org_id == context.org_id,
            OrganizationMember.user_id == context.user_id,
        )
        .values(status="suspended")
    )
    await db.commit()


@pytest.mark.parametrize("operation", ["create", "update", "delete"])
async def test_trigger_writer_denies_revoked_membership(
    db: AsyncSession,
    private_scopes: PrivateScopes,
    operation: Literal["create", "update", "delete"],
) -> None:
    scope = private_scopes.first
    with pytest.MonkeyPatch.context() as patches:
        patches.setattr(trigger_service, "sync_scheduler_for_trigger", AsyncMock())
        targets = await _targets(db, scope)
        await _suspend(db, scope)
        match operation:
            case "create":
                pending = trigger_service.create_trigger(
                    db,
                    targets.agent.id,
                    scope,
                    TriggerCreate(
                        trigger_type="interval",
                        schedule_config={"interval_minutes": 20},
                        input_message="Denied",
                    ),
                )
            case "update":
                pending = trigger_service.update_trigger(
                    db,
                    targets.trigger,
                    TriggerUpdate(status="paused", name="Denied"),
                    context=scope,
                )
            case "delete":
                pending = trigger_service.delete_trigger(db, targets.trigger, scope)
            case unreachable:
                assert_never(unreachable)
        with pytest.raises(AppError):
            await pending
    await db.commit()
    name = await db.scalar(select(AgentTrigger.name).where(AgentTrigger.id == targets.trigger.id))
    assert name == targets.trigger.name


@pytest.mark.parametrize("operation", ["favorite", "open", "download", "delete"])
async def test_artifact_writer_denies_revoked_membership(
    db: AsyncSession,
    private_scopes: PrivateScopes,
    operation: Literal["favorite", "open", "download", "delete"],
) -> None:
    scope = private_scopes.first
    with pytest.MonkeyPatch.context() as patches:
        patches.setattr(trigger_service, "sync_scheduler_for_trigger", AsyncMock())
        targets = await _targets(db, scope)
    await _suspend(db, scope)
    match operation:
        case "favorite":
            pending = library.set_artifact_favorite(
                db, context=scope, artifact_id=targets.artifact.id, is_favorite=True
            )
        case "open":
            pending = library.record_artifact_opened(
                db, context=scope, artifact_id=targets.artifact.id
            )
        case "download":
            pending = library.record_artifact_download(
                db, context=scope, artifact_id=targets.artifact.id
            )
        case "delete":
            pending = library.delete_artifact(db, context=scope, artifact_id=targets.artifact.id)
        case unreachable:
            assert_never(unreachable)
    with pytest.raises(AppError):
        await pending
    await db.commit()
    row = (
        await db.execute(
            select(
                ConversationArtifact.status,
                ConversationArtifact.is_favorite,
                ConversationArtifact.preview_count,
                ConversationArtifact.download_count,
            ).where(ConversationArtifact.id == targets.artifact.id)
        )
    ).one()
    assert (
        row.status == "ready"
        and not row.is_favorite
        and row.preview_count == 0
        and row.download_count == 0
    )
