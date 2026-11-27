"""Correlate staged human messages with the actual root chat model call."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import TypedDict
from uuid import UUID, uuid5

from langchain.agents.middleware.types import ExtendedModelResponse, ModelRequest, ModelResponse
from langgraph.types import Command

from app.agent_runtime.model_input_receipt import observe_message_delivery
from app.schemas.conversation_graph_input import STEER_RECEIPTS_CHANNEL


class SteerReceipt(TypedDict):
    run_id: str
    staged: dict[str, str]
    ids: list[str]


def steer_message_id(steer_id: UUID) -> str:
    """Keep the chat message ID stable without exposing its delivery ledger ID."""
    return str(uuid5(steer_id, "moldy.chat.run-steer.message.v1"))


def run_receipt(value: object, run_id: UUID) -> SteerReceipt | None:
    """Ignore a prior run's receipt. Hold malformed data for reconciliation."""
    empty: SteerReceipt = {"run_id": str(run_id), "staged": {}, "ids": []}
    if value is None:
        return empty
    if not isinstance(value, dict) or not isinstance(value.get("run_id"), str):
        return None
    if value["run_id"] != str(run_id):
        return empty
    staged, ids = value.get("staged", {}), value.get("ids")
    if not isinstance(staged, dict) or not isinstance(ids, list):
        return None
    if not all(isinstance(digest, str) for digest in staged.values()):
        return None
    try:
        if not all(isinstance(raw, str) and str(UUID(raw)) == raw for raw in [*staged, *ids]):
            return None
    except ValueError:
        return None
    return {"run_id": str(run_id), "staged": dict(staged), "ids": list(ids)}


async def observe_model_delivery(
    request: ModelRequest,
    handler: Callable[[ModelRequest], Awaitable[ModelResponse]],
    receipt: SteerReceipt,
) -> ModelResponse | ExtendedModelResponse:
    expected = {
        steer_message_id(UUID(raw)): (raw, digest) for raw, digest in receipt["staged"].items()
    }
    response, matches = await observe_message_delivery(request, handler, expected)
    observed = set().union(*matches.values())
    if not observed:
        return response
    staged = receipt["staged"]
    applied: SteerReceipt = {
        "run_id": receipt["run_id"],
        "staged": {raw: digest for raw, digest in staged.items() if raw not in observed},
        "ids": [*receipt["ids"], *(raw for raw in staged if raw in observed)],
    }
    # A short-circuit return, a changed prompt or a crash before this model node
    # commits leaves the separate pre-model staging record unconfirmed.
    return ExtendedModelResponse(
        model_response=response,
        command=Command(update={STEER_RECEIPTS_CHANNEL: applied}),
    )
