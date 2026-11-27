"""Deliver accepted chat steers at a root model boundary in the owning run."""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable, Collection, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Annotated, Any, NotRequired
from uuid import UUID

from langchain.agents.middleware import AgentMiddleware, AgentState
from langchain.agents.middleware.types import (
    ExtendedModelResponse,
    ModelRequest,
    ModelResponse,
    PrivateStateAttr,
)
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, ToolMessage
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.config import get_config
from langgraph.runtime import Runtime
from sqlalchemy.exc import SQLAlchemyError

from app.agent_runtime.chat_steer_checkpoint import confirmed_steer_ids
from app.agent_runtime.chat_steer_model_delivery import (
    SteerReceipt,
    observe_model_delivery,
    run_receipt,
    steer_message_id,
)
from app.agent_runtime.model_input_receipt import text_digest
from app.agent_runtime.prepared_run_write_capability import (
    PreparedRunWriteCapability,
    RunWriteBinding,
    RunWriteCapabilityDeniedError,
)
from app.config import settings
from app.database import SessionTenant, tenant_session
from app.schemas.conversation_graph_input import STEER_INPUT_MARKER, STEER_RECEIPTS_CHANNEL

if TYPE_CHECKING:
    from app.agent_runtime.chat_steer_protocol import ChatSteerStore
    from app.agent_runtime.runtime_config import AgentConfig

logger = logging.getLogger(__name__)


class RunSteerState(AgentState[Any]):
    moldy_run_steer_receipts: NotRequired[Annotated[SteerReceipt, PrivateStateAttr]]


@dataclass(frozen=True, slots=True)
class _Run:
    conversation_id: UUID
    run_id: UUID
    tenant_id: UUID
    saver: BaseCheckpointSaver


def tool_results_complete(messages: Sequence[BaseMessage]) -> bool:
    """Never insert a user message between sibling tool calls and their results."""
    answered: set[str] = set()
    for message in reversed(messages):
        if isinstance(message, ToolMessage):
            answered.add(message.tool_call_id)
        elif isinstance(message, AIMessage):
            return all(call.get("id") in answered for call in message.tool_calls)
    return True


def _root_run() -> _Run | None:
    config = get_config().get("configurable", {})
    namespace = config.get("checkpoint_ns")
    saver = config.get("__pregel_checkpointer")
    # A middleware node in a child graph has at least one enclosing graph path.
    if (
        not isinstance(namespace, str)
        or "|" in namespace
        or not isinstance(saver, BaseCheckpointSaver)
    ):
        return None
    try:
        return _Run(
            UUID(str(config["thread_id"])),
            UUID(str(config["moldy_run_id"])),
            UUID(str(config["moldy_tenant_id"])),
            saver,
        )
    except (KeyError, ValueError):
        return None


class RunSteerMiddleware(AgentMiddleware[RunSteerState]):
    state_schema = RunSteerState

    def __init__(
        self, store: ChatSteerStore, capability: PreparedRunWriteCapability | None = None
    ) -> None:
        super().__init__()
        self._store = store
        self._capability = capability
        # Only an optimization: a fresh worker always consults the durable saver.
        self._confirmed: set[UUID] = set()

    def _bound_run(self) -> _Run | None:
        run = _root_run()
        if run is not None and self._capability is not None:
            self._capability.require_run(run.run_id)
            self._capability.require_conversation(run.conversation_id)
            if self._capability.binding.tenant_id != run.tenant_id:
                raise RunWriteCapabilityDeniedError("tenant")
        return run

    async def abefore_model(self, state: RunSteerState, runtime: Runtime) -> dict[str, Any] | None:
        run = self._bound_run()
        if run is None or not tool_results_complete(state["messages"]):
            return None
        receipt = run_receipt(state.get("moldy_run_steer_receipts"), run.run_id)
        if receipt is None:
            return None
        seen = [UUID(raw) for raw in receipt["ids"]]
        try:
            async with tenant_session(SessionTenant(run.tenant_id)) as db:
                rows = await self._store.claim(
                    db,
                    conversation_id=run.conversation_id,
                    run_id=run.run_id,
                    checkpointed_ids=seen,
                )
                messages = [
                    HumanMessage(
                        content=row.text,
                        id=steer_message_id(row.id),
                        response_metadata={STEER_INPUT_MARKER: True},
                    )
                    for row in rows
                ]
                receipt["staged"].update({str(row.id): text_digest(row.text) for row in rows})
                await db.commit()
        except SQLAlchemyError:
            logger.warning("Chat steer claim failed for run %s", run.run_id, exc_info=True)
            return None
        if not messages:
            return None
        # The private channel and stable messages share the same graph update.
        return {
            "messages": messages,
            STEER_RECEIPTS_CHANNEL: receipt,
        }

    async def awrap_model_call(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], Awaitable[ModelResponse]],
    ) -> ModelResponse | ExtendedModelResponse:
        run = self._bound_run()
        if run is None:
            return await handler(request)
        receipt = run_receipt(request.state.get(STEER_RECEIPTS_CHANNEL), run.run_id)
        if not receipt or not receipt["staged"]:
            return await handler(request)
        return await observe_model_delivery(request, handler, receipt)

    async def aafter_model(self, state: RunSteerState, runtime: Runtime) -> None:
        run = self._bound_run()
        if run is None:
            return
        receipt = run_receipt(state.get("moldy_run_steer_receipts"), run.run_id)
        if not receipt:
            return
        outstanding = {UUID(raw) for raw in receipt["ids"]}.difference(self._confirmed)
        if outstanding:
            await self._confirm(run, outstanding)

    async def _confirm(self, run: _Run, outstanding: Collection[UUID]) -> None:
        # The graph's in-memory view can lead its async saver. Never acknowledge a
        # claim from that view. The terminal path repeats this check after shutdown.
        proven = await confirmed_steer_ids(
            run.conversation_id,
            run.run_id,
            outstanding,
            saver=run.saver,
            tenant_id=run.tenant_id,
        )
        if not proven:
            return
        try:
            async with tenant_session(SessionTenant(run.tenant_id)) as db:
                await self._store.confirm(
                    db, conversation_id=run.conversation_id, run_id=run.run_id, steer_ids=proven
                )
                await db.commit()
        except SQLAlchemyError:
            logger.warning("Chat steer acknowledgment failed for run %s", run.run_id, exc_info=True)
            return
        self._confirmed.update(proven)


def root_steer_middleware(
    cfg: AgentConfig,
    *,
    run_id: str | None,
    is_trigger_mode: bool,
    prepared_output_root: Path | None = None,
) -> tuple[RunSteerMiddleware, ...]:
    if (
        settings.chat_steer_enabled
        and cfg.steer_store is not None
        and run_id is not None
        and cfg.run_path == "chat"
        and cfg.runtime_profile == "standard"
        and not is_trigger_mode
    ):
        capability = None
        if prepared_output_root is not None:
            capability = PreparedRunWriteCapability(
                RunWriteBinding(
                    run_id=UUID(run_id),
                    conversation_id=UUID(str(cfg.thread_id)),
                    user_id=cfg.org_context.user_id,
                    org_id=cfg.org_context.org_id,
                    tenant_id=cfg.org_context.tenant_id,
                    output_root=prepared_output_root,
                )
            )
        return (RunSteerMiddleware(cfg.steer_store, capability),)
    return ()
