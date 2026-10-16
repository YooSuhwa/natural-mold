"""Prepared ``task`` execution and installed-compatible result projection (I12).

Runs the selected precompiled child exactly once under its won reservation and
projects the result as the installed ``SubAgentMiddleware`` would: a
``Command`` updating non-excluded, non-private state plus one ``ToolMessage``
carrying the exact invocation ID. Owns completion and cancellation cleanup:
the reservation is finished exactly once, a parent cancellation cancels the
child task, and a ledger terminalization surfaces as a dispatch denial.

The child config overrides the parent's response-format token (``None``), so a
nested interceptor never inherits it through LangGraph's configurable merge,
and carries the invoking task path, from which nested levels derive their
``parent_task_id``.
"""

from __future__ import annotations

import asyncio
import dataclasses
import json
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Annotated, Any, NotRequired, Required, get_args, get_origin, get_type_hints

from deepagents.middleware.memory import MemoryState
from deepagents.middleware.skills import SkillsState
from deepagents.middleware.subagents import SUBAGENT_RESPONSE_FORMAT_CONFIG_KEY
from langchain.agents.middleware.types import PrivateStateAttr
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langgraph.types import Command

from app.agent_runtime.lease_dispatch_permit import DispatchDeniedError
from app.agent_runtime.prepared_child_launch import ChildLaunchKey, ChildLaunchReservation

EXCLUDED_STATE_KEYS = frozenset({"messages", "todos", "structured_response"})
TASK_PATH_KEY = "_moldy_prepared_task_path"
TERMINALIZED = "prepared_run_terminalized"


def child_task_path(parent_task_id: str, invocation_id: str) -> str:
    """Unambiguous path of one task invocation (length-prefixed invocation ID)."""

    return f"{parent_task_id}/{len(invocation_id)}:{invocation_id}"


def child_config(key: ChildLaunchKey) -> dict[str, Any]:
    path = child_task_path(key.parent_task_id, key.invocation_id)
    configurable = {
        "ls_agent_type": "subagent",
        SUBAGENT_RESPONSE_FORMAT_CONFIG_KEY: None,
        TASK_PATH_KEY: path,
    }
    return {"configurable": configurable}


def private_state_keys(*schemas: type[object]) -> frozenset[str]:
    """Return ``PrivateStateAttr`` fields, as the installed task tool strips them."""

    names: set[str] = set()
    for schema in schemas:
        for name, hint in get_type_hints(schema, include_extras=True).items():
            while get_origin(hint) is NotRequired or get_origin(hint) is Required:
                hint = get_args(hint)[0]
            if get_origin(hint) is Annotated and PrivateStateAttr in get_args(hint)[1:]:
                names.add(name)
    return frozenset(names)


def _default_private_keys() -> frozenset[str]:
    return private_state_keys(SkillsState, MemoryState)


@dataclass(frozen=True, slots=True)
class PreparedStateProjector:
    """Projects parent state into child input and child output into an update."""

    private_state_keys: frozenset[str] = dataclasses.field(default_factory=_default_private_keys)

    def _hidden(self, key: str) -> bool:
        return key in EXCLUDED_STATE_KEYS or key in self.private_state_keys

    def child_input(self, state: Mapping[str, Any], description: str) -> dict[str, Any]:
        projected = {k: v for k, v in state.items() if not self._hidden(k)}
        projected["messages"] = [HumanMessage(content=description)]
        return projected

    def command(self, result: Mapping[str, Any], invocation_id: str) -> Command:
        if "messages" not in result:
            raise ValueError("prepared child must return a state containing 'messages'")
        update = {k: v for k, v in result.items() if not self._hidden(k)}
        return Command(
            update={
                **update,
                "messages": [ToolMessage(_content(result), tool_call_id=invocation_id)],
            }
        )


def _content(result: Mapping[str, Any]) -> str:
    structured = result.get("structured_response")
    if structured is not None:
        if hasattr(structured, "model_dump_json"):
            return structured.model_dump_json()
        if dataclasses.is_dataclass(structured) and not isinstance(structured, type):
            return json.dumps(dataclasses.asdict(structured))
        return json.dumps(structured)
    for message in reversed(result["messages"]):
        if isinstance(message, AIMessage):
            text = message.text.rstrip() if message.text else ""
            if text:
                return text
    return ""


async def run_prepared_child(
    reservation: ChildLaunchReservation,
    runnable: Any,
    child_input: dict[str, Any],
    *,
    invocation_id: str,
    projector: PreparedStateProjector,
) -> Command:
    """Await the selected precompiled runnable once and project its result."""

    try:
        config = child_config(reservation.key)
        child = reservation.launch(lambda: runnable.ainvoke(child_input, config))
    except BaseException:
        reservation.release()
        raise
    try:
        result = await child
    except asyncio.CancelledError:
        current = asyncio.current_task()
        if reservation.terminalized and (current is None or current.cancelling() == 0):
            raise DispatchDeniedError(TERMINALIZED) from None
        raise
    finally:
        if not child.done():
            child.cancel()
        reservation.finish()
    return projector.command(result, invocation_id)
