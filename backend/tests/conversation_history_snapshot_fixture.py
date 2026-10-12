from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID, uuid4

from langchain_core.messages import AIMessage, HumanMessage
from langgraph.checkpoint.base import empty_checkpoint
from langgraph.checkpoint.memory import InMemorySaver
from sqlalchemy.ext.asyncio import AsyncSession

from app.authz.agent_lifecycle_scope import (
    AgentLifecycleAuthority,
    AgentLifecycleAuthorization,
)
from app.credentials.service import encrypt_data
from app.dependencies import CurrentUser
from app.models.agent import Agent
from app.models.conversation import Conversation
from app.models.credential import Credential
from app.models.message_event import MessageEvent
from app.models.model import Model
from app.services.conversation_history_secrets import HistorySecretSourceError
from app.services.conversation_history_snapshot import ConversationHistoryMaterializationRequest
from tests.authz.unit_scope_fixture import TEST_ORG_ID, TEST_TENANT_ID
from tests.conftest import TEST_USER_ID, TestSession

AGENT_ID = UUID("10000000-0000-0000-0000-000000000007")
SECRET = "opaque-task7-secret-value"


@dataclass(frozen=True, slots=True)
class SnapshotCase:
    conversation: Conversation
    saver: InMemorySaver


async def snapshot_case() -> SnapshotCase:
    conversation = await _conversation()
    return SnapshotCase(conversation, await _branched_saver(conversation.id))


async def namespaced_snapshot_case() -> SnapshotCase:
    conversation = await _conversation()
    return SnapshotCase(conversation, await _namespaced_saver(conversation.id))


def actor() -> CurrentUser:
    return CurrentUser(id=TEST_USER_ID, email="admin@moldy.test", name="Admin")


def materialization_request(
    db: AsyncSession,
    case: SnapshotCase,
) -> ConversationHistoryMaterializationRequest:
    return ConversationHistoryMaterializationRequest(
        db,
        actor(),
        AGENT_ID,
        case.conversation.id,
        "Snapshot Agent",
        case.saver,
    )


async def authorize_snapshot_agent(
    _db: AsyncSession,
    _actor: CurrentUser,
    _authorization: AgentLifecycleAuthorization,
) -> AgentLifecycleAuthority:
    return AgentLifecycleAuthority(
        agent_id=AGENT_ID,
        name="Snapshot Agent",
        owner_user_id=TEST_USER_ID,
        owner_label="Owner",
        archived_by_user_id=None,
        archived_at=None,
        lifecycle_state="active",
        policy_delete_at=None,
        can_archive=True,
        can_restore=True,
        can_purge=True,
        actor_user_id=TEST_USER_ID,
        actor_email="admin@moldy.test",
        actor_label="Admin",
    )


async def collect_fixture_secret(
    _db: AsyncSession,
    _agent_id: UUID,
    _conversation_id: UUID,
) -> set[str]:
    return {SECRET}


async def collect_no_secrets(
    _db: AsyncSession,
    _agent_id: UUID,
    _conversation_id: UUID,
) -> set[str]:
    return set()


async def fail_secret_collection(
    _db: AsyncSession,
    _agent_id: UUID,
    _conversation_id: UUID,
) -> set[str]:
    raise HistorySecretSourceError("fixture failure")


async def seed_external_secret_source(
    db: AsyncSession,
    conversation_id: UUID,
) -> MessageEvent:
    encrypted, key_id, field_keys = encrypt_data(
        {"api_key": {"__external__": {"provider": "env", "ref": "TASK7_TYPED_FAILURE"}}}
    )
    model = Model(
        provider="anthropic",
        model_name="history-external-error",
        display_name="History external error",
        is_default=False,
        is_visible=False,
        source="manual",
    )
    credential = Credential(
        tenant_id=TEST_TENANT_ID,
        org_id=TEST_ORG_ID,
        user_id=TEST_USER_ID,
        definition_key="anthropic",
        name="History external error",
        data_encrypted=encrypted,
        key_id=key_id,
        field_keys=field_keys,
        status="active",
        is_system=False,
    )
    source_event = MessageEvent(
        conversation_id=conversation_id,
        assistant_msg_id=f"source-{uuid4()}",
        events=[{"id": "source-1", "event": "message_end", "data": {"content": "safe"}}],
    )
    db.add_all([model, credential])
    await db.flush()
    db.add(
        Agent(
            id=AGENT_ID,
            tenant_id=TEST_TENANT_ID,
            org_id=TEST_ORG_ID,
            user_id=TEST_USER_ID,
            name="Snapshot Agent",
            system_prompt="history",
            model_id=model.id,
            llm_credential_id=credential.id,
            status="active",
        )
    )
    db.add(source_event)
    await db.commit()
    return source_event


async def _conversation() -> Conversation:
    conversation = Conversation(
        id=uuid4(),
        agent_id=AGENT_ID,
        user_id=TEST_USER_ID,
        org_id=TEST_ORG_ID,
        tenant_id=TEST_TENANT_ID,
        agent_name_snapshot="Snapshot Agent",
        last_activity_at=datetime.now(UTC),
    )
    async with TestSession() as db:
        db.add(conversation)
        await db.commit()
    return conversation


async def _branched_saver(conversation_id: UUID) -> InMemorySaver:
    saver = InMemorySaver()
    root = empty_checkpoint()
    root["id"] = "checkpoint-root"
    root["channel_values"] = {
        "messages": [HumanMessage(id="message-user", content=f"question {SECRET}")],
        "branch_header": {"name": "root"},
    }
    root["channel_versions"] = {"messages": "1", "branch_header": "1"}
    saved = await saver.aput(
        {"configurable": {"thread_id": str(conversation_id), "checkpoint_ns": ""}},
        root,
        {"source": "input", "step": 0},
        {"messages": "1", "branch_header": "1"},
    )
    branch = empty_checkpoint()
    branch["id"] = "checkpoint-branch"
    branch["channel_values"] = {
        "messages": [
            HumanMessage(id="message-user", content=f"question {SECRET}"),
            AIMessage(id="message-ai", content=f"answer {SECRET}"),
        ],
        "branch_header": {"name": "branch", "parent": "checkpoint-root"},
    }
    branch["channel_versions"] = {"messages": "2", "branch_header": "2"}
    await saver.aput(
        saved,
        branch,
        {"source": "loop", "step": 1},
        {"messages": "2", "branch_header": "2"},
    )
    return saver


async def _namespaced_saver(conversation_id: UUID) -> InMemorySaver:
    saver = InMemorySaver()
    for namespace in ("alpha", "beta"):
        checkpoint = empty_checkpoint()
        checkpoint["id"] = "shared-checkpoint"
        checkpoint["channel_values"] = {
            "messages": [HumanMessage(id=f"{namespace}-message", content=namespace)],
            "marker": f"{namespace}-value",
        }
        checkpoint["channel_versions"] = {"messages": "1", "marker": "1"}
        await saver.aput(
            {"configurable": {"thread_id": str(conversation_id), "checkpoint_ns": namespace}},
            checkpoint,
            {"source": "input", "step": 0},
            {"messages": "1", "marker": "1"},
        )
    for namespace in ("alpha", "beta"):
        stored = await saver.aget_tuple(
            {
                "configurable": {
                    "thread_id": str(conversation_id),
                    "checkpoint_id": "shared-checkpoint",
                    "checkpoint_ns": namespace,
                }
            }
        )
        assert stored is not None
        stored.checkpoint["channel_values"].pop("messages")
    return saver


__all__ = [
    "AGENT_ID",
    "SECRET",
    "SnapshotCase",
    "actor",
    "authorize_snapshot_agent",
    "collect_fixture_secret",
    "collect_no_secrets",
    "fail_secret_collection",
    "materialization_request",
    "namespaced_snapshot_case",
    "seed_external_secret_source",
    "snapshot_case",
]
