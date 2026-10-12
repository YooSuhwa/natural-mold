from __future__ import annotations

from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path
from uuid import UUID, uuid4

from sqlalchemy.ext.asyncio import AsyncSession

from app.dependencies import CurrentUser
from app.models.agent_purge import AgentPurgeCleanupItem, AgentPurgeReceipt
from app.models.conversation import Conversation
from app.models.conversation_history import ConversationHistoryManifest
from app.models.user import User
from app.services.conversation_history_projection import canonical_payload_sha256
from tests.authz.unit_scope_fixture import TEST_ORG_ID, TEST_TENANT_ID, ensure_unit_scope
from tests.conftest import TEST_USER_ID


class RecordingSaver:
    def __init__(self) -> None:
        self.deleted_threads: list[str] = []

    async def adelete_thread(self, thread_id: str) -> None:
        self.deleted_threads.append(thread_id)


async def seed_cleanup_receipt(
    db: AsyncSession,
    kind: str,
    locator: str,
) -> tuple[AgentPurgeReceipt, AgentPurgeCleanupItem]:
    actor = CurrentUser(id=TEST_USER_ID, email="test@test.com", name="Test User")
    await ensure_unit_scope(db, actor)
    user = await db.get(User, TEST_USER_ID)
    assert user is not None
    agent_id = uuid4()
    receipt = AgentPurgeReceipt(
        tenant_id=TEST_TENANT_ID,
        org_id=TEST_ORG_ID,
        agent_id_snapshot=agent_id,
        owner_user_id_snapshot=user.id,
        agent_name_snapshot="Deleted agent",
        requester_user_id=user.id,
        state="db_deleted",
    )
    db.add(receipt)
    await db.flush()
    if kind == "raw_checkpoint":
        conversation_id = UUID(locator)
        db.add(
            Conversation(
                id=conversation_id,
                tenant_id=TEST_TENANT_ID,
                org_id=TEST_ORG_ID,
                agent_id=None,
                user_id=user.id,
                agent_name_snapshot="Deleted agent",
                agent_lifecycle_state="purged",
                agent_purged_at=datetime.now(UTC),
                last_activity_at=datetime.now(UTC),
            )
        )
        await db.flush()
        db.add(
            ConversationHistoryManifest(
                conversation_id=conversation_id,
                source_agent_id_snapshot=agent_id,
                state="complete",
                checkpoint_count=0,
                event_count=0,
                canonical_payload_sha256=canonical_payload_sha256(
                    {
                        "schema_version": 1,
                        "conversation_id": str(conversation_id),
                        "source_agent_id": str(agent_id),
                        "checkpoints": [],
                        "events": [],
                    }
                ),
            )
        )
    item = AgentPurgeCleanupItem(
        receipt_id=receipt.id,
        kind=kind,
        locator=locator,
        locator_sha256=sha256(locator.encode()).hexdigest(),
    )
    db.add(item)
    await db.commit()
    return receipt, item


def write_outside_file(root: Path) -> Path:
    target = root / "must-survive.txt"
    target.write_text("conversation-owned bytes", encoding="utf-8")
    return target


__all__ = ["RecordingSaver", "seed_cleanup_receipt", "write_outside_file"]
