"""Multi-transaction orchestration for idempotent Agent purge."""

from __future__ import annotations

from app.agent_runtime.checkpointer import get_checkpointer
from app.exceptions import AppError
from app.models.agent_purge import AgentPurgeReceipt
from app.schemas.agent_lifecycle import AgentPurgeReceiptResponse
from app.services.agent_purge_cleanup import process_agent_purge_cleanup
from app.services.agent_purge_preparation import prepare_agent_purge
from app.services.agent_purge_relational import finalize_agent_purge
from app.services.agent_purge_types import AgentPurgeCommand, PreparedAgentPurge
from app.services.conversation_history_snapshot import materialize_conversation_history
from app.services.conversation_history_types import ConversationHistoryMaterializationRequest


def _response(receipt: AgentPurgeReceipt) -> AgentPurgeReceiptResponse:
    return AgentPurgeReceiptResponse.model_validate(
        {
            "receipt_id": receipt.id,
            "agent_id": receipt.agent_id_snapshot,
            "state": receipt.state,
            "created_at": receipt.created_at,
            "updated_at": receipt.updated_at,
        }
    )


async def _recover_committed_purge(
    command: AgentPurgeCommand,
    error: AppError,
) -> PreparedAgentPurge:
    if error.status != 404:
        raise error
    await command.db.rollback()
    prepared = await prepare_agent_purge(command)
    await command.db.commit()
    if not prepared.database_deleted:
        raise error
    return prepared


async def purge_agent(command: AgentPurgeCommand) -> AgentPurgeReceiptResponse:
    """Run receipt, snapshot, DB deletion, and retryable cleanup in strict order."""
    prepared = await prepare_agent_purge(command)
    await command.db.commit()

    if not prepared.database_deleted:
        try:
            if prepared.conversation_ids:
                checkpointer = command.checkpointer or get_checkpointer()
                for conversation_id in prepared.conversation_ids:
                    await materialize_conversation_history(
                        ConversationHistoryMaterializationRequest(
                            command.db,
                            command.actor,
                            command.agent_id,
                            conversation_id,
                            command.confirmation_name,
                            checkpointer,
                        )
                    )
                    await command.db.commit()
            await finalize_agent_purge(command, prepared)
            await command.db.commit()
        except AppError as error:
            prepared = await _recover_committed_purge(command, error)

    receipt = await process_agent_purge_cleanup(command.db, prepared.receipt_id)
    await command.db.commit()
    return _response(receipt)


__all__ = ["purge_agent"]
