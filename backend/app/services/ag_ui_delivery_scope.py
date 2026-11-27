"""Private command receipts are discovered by identity before their payload may be read."""

from uuid import UUID

from sqlalchemy import Select, and_, inspect, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm.base import NO_VALUE
from sqlalchemy.sql.elements import ColumnElement

from app.authz.scoping import same_private_scope
from app.exceptions import ConflictError
from app.models.ag_ui_command_receipt import AgUiCommandReceipt
from app.models.conversation import Conversation
from app.models.conversation_run import ConversationRun

DELIVERY_SOURCES = ("resume", "edit", "regenerate")


def _receipt_matches_run() -> ColumnElement[bool]:
    return and_(
        AgUiCommandReceipt.run_id == ConversationRun.id,
        AgUiCommandReceipt.conversation_id == ConversationRun.conversation_id,
        same_private_scope(ConversationRun, AgUiCommandReceipt),
        AgUiCommandReceipt.input_id.is_(None),
        AgUiCommandReceipt.delivery_encrypted.is_not(None),
    )


def _unclaimed_run() -> ColumnElement[bool]:
    return and_(
        ConversationRun.source.in_(DELIVERY_SOURCES),
        ConversationRun.status == "queued",
        ConversationRun.is_active.is_(True),
        ConversationRun.worker_instance_id.is_(None),
        ConversationRun.started_at.is_(None),
        ConversationRun.owner_generation == 0,
        ConversationRun.owner_token.is_(None),
        ConversationRun.runtime_materialized_at.is_(None),
        ConversationRun.cancel_requested_at.is_(None),
    )


def _conversation_matches_receipt() -> ColumnElement[bool]:
    return and_(
        Conversation.id == AgUiCommandReceipt.conversation_id,
        same_private_scope(Conversation, AgUiCommandReceipt),
    )


def recoverable_private_delivery() -> ColumnElement[bool]:
    """The startup stale sweep precedes queue recovery. Keep unstarted durable inputs."""
    return and_(
        _unclaimed_run(),
        select(AgUiCommandReceipt.run_id)
        .join(Conversation, _conversation_matches_receipt())
        .where(_receipt_matches_run())
        .exists(),
    )


async def require_mutable_branch(db: AsyncSession, conversation_id: UUID) -> None:
    """The caller holds the private Conversation lock through its branch or graph mutation."""
    pending = select(ConversationRun.id).where(
        ConversationRun.conversation_id == conversation_id, recoverable_private_delivery()
    )
    if await db.scalar(select(pending.exists())):
        raise ConflictError(
            "AG_UI_BRANCH_IN_USE", "접수된 작업을 시작하는 동안에는 대화 분기를 바꿀 수 없습니다."
        )


def bound_deliveries() -> Select[tuple[ConversationRun]]:
    from app.services.ag_ui_commands import PROJECTION_VERSION

    return (
        select(ConversationRun)
        .select_from(ConversationRun)
        .join(AgUiCommandReceipt, _receipt_matches_run())
        .join(Conversation, _conversation_matches_receipt())
        .where(
            _unclaimed_run(),
            AgUiCommandReceipt.projection_version == PROJECTION_VERSION,
        )
    )


async def clear_private_delivery(db: AsyncSession, run: ConversationRun) -> None:
    """Retain the idempotency receipt, discard its private command once terminal."""
    # Some cleanup paths intentionally load identity-only rows with raiseload.
    # A known ordinary source avoids a receipt query on the common chat path.
    loaded_source = inspect(run).attrs.source.loaded_value
    if loaded_source is not NO_VALUE and loaded_source not in DELIVERY_SOURCES:
        return
    scope = (
        select(ConversationRun.id)
        .where(ConversationRun.id == run.id, _receipt_matches_run())
        .correlate(AgUiCommandReceipt)
    )
    await db.execute(
        update(AgUiCommandReceipt)
        .where(AgUiCommandReceipt.run_id == run.id, scope.exists())
        .values(delivery_encrypted=None)
        .execution_options(synchronize_session=False)
    )
