"""``ConnectionGuardMiddleware`` — the pre-call check of plan 11.4 (F-07).

* ``aafter_model`` (before the ToolNode) looks at every tool call of the last
  ``AIMessage`` at once: calls outside the run's plan get a typed refusal ToolMessage
  (same ``tool_call_id``, so the ToolNode skips them; allowed siblings run unchanged),
  then the credentials of the remaining calls are re-read. Only a visible credential
  that is not usable (anything but ``active``/``auth_needed`` — ``auth_needed`` keeps
  F-05's refresh path) or a per_user binding that disappeared is a problem; an unknown
  state skips the pre-check (the leaf call fails closed). Problems become ONE
  ``auth_required`` interrupt before anything runs.
* ``abefore_model`` (after the ToolNode, all siblings finished) lifts the F-12
  ``mcp_auth_error`` artifacts of the step into ONE bundled interrupt, so nothing is
  interrupted inside the ToolNode (no re-execution of finished siblings on resume).
* ``awrap_tool_call`` is the last re-check: refused names, tools the graph does not
  know, and a fresh FGA decision for credentialed calls on rows the run does not own.

Modes (``ConnectionGuardPolicy.mode``): ``interrupt`` (user-facing root run), ``raise``
(triggers, invoke API: ``ConnectionBlockedError(auth_required)`` ends the run blocked),
``refuse`` (subagent graphs: pre-call problems are ``auth_required`` refusals, post-call
auth errors stay error ToolMessages; never an interrupt or a raise — the subagent runs
inside the parent's ToolNode). ``fixable_by=owner`` problems never interrupt the runner:
the owner is notified once per (run, credential). Every interrupt carries ``attempt``
(1, 2, ... within one node execution) because LangGraph derives the interrupt id from the
task namespace, so a still-broken ``retry`` re-asks under the same id. Resume (11.5):
``retry`` re-reads (pre-call) or lets the model call again on the re-resolved run
(post-call), ``skip`` answers the calls as skipped, ``cancel`` ends the run.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Sequence
from typing import Any

from langchain.agents.middleware import AgentMiddleware, AgentState, hook_config
from langchain.agents.middleware.types import ToolCallRequest
from langchain_core.messages import AIMessage, AnyMessage, ToolMessage
from langchain_core.messages.tool import ToolCall
from langchain_core.tools import BaseTool
from langgraph.config import get_config
from langgraph.runtime import Runtime
from langgraph.types import Command

from app.agent_runtime.runtime.connection_guard_items import (
    ConnectionBlockedError,
    auth_interrupt_of,
    bundle,
    notify_owner,
    refusal,
    resolved,
)
from app.agent_runtime.runtime.connection_guard_policy import (
    ConnectionGuardPolicy,
    GuardMode,
    build_connection_guard_policy,
)
from app.agent_runtime.runtime.connection_guard_resume import ask, recorded_answers
from app.authz.runtime_access_planner import runtime_access_enforced

GUARD_NAME = "ConnectionGuardMiddleware"
Problem = tuple[ToolCall, str, dict[str, Any]]  # call, reason, 11.5 item


def _last_step(messages: Sequence[AnyMessage]) -> tuple[AIMessage | None, list[ToolMessage]]:
    for index in range(len(messages) - 1, -1, -1):
        message = messages[index]
        if isinstance(message, AIMessage):
            trailing = [m for m in messages[index + 1 :] if isinstance(m, ToolMessage)]
            return message, trailing
    return None, []


USABLE = frozenset({"active", "auth_needed"})


def _run_key() -> str:
    try:
        configurable = get_config().get("configurable", {})
    except RuntimeError:
        return "unknown"
    return str(configurable.get("moldy_run_id") or configurable.get("thread_id") or "unknown")


class ConnectionGuardMiddleware(AgentMiddleware):
    def __init__(self, policy: ConnectionGuardPolicy) -> None:
        super().__init__()
        self.policy = policy
        self._name = GUARD_NAME

    @property
    def name(self) -> str:
        return self._name

    @property
    def _tenants(self) -> dict[str, str]:
        """Company of each guarded credential (owner notifications use tenant sessions)."""
        return {t.credential_id: t.tenant_id for t in self.policy.guarded.values() if t.tenant_id}

    def take_slot(self, name: str) -> None:
        """Run in another middleware's stack slot (``connection_guard_order``)."""
        self._name = name

    # -- before the ToolNode -------------------------------------------------------
    @hook_config(can_jump_to=["end"])
    async def aafter_model(self, state: AgentState, runtime: Runtime) -> dict[str, Any] | None:
        ai, answered = _last_step(state["messages"])
        if ai is None or not ai.tool_calls:
            return None
        done = {message.tool_call_id for message in answered}
        pending = [call for call in ai.tool_calls if call["id"] not in done]
        refused = [refusal(c, code) for c in pending if (code := self.policy.denied.get(c["name"]))]
        skip = {message.tool_call_id for message in refused}
        pending = [call for call in pending if call["id"] not in skip]
        messages: list[ToolMessage] = list(refused)
        jump: str | None = None
        problems, attempt, recorded = await self._problems(pending), 0, recorded_answers()
        while problems or attempt < recorded:  # G-03: replay a waiting answer
            done |= {message.tool_call_id for message in messages}
            owner = [p for p in problems if p[2]["fixable_by"] == "owner"]
            owner = [p for p in owner if p[0]["id"] not in done]
            await notify_owner([i for _, _, i in owner], run=_run_key(), tenants=self._tenants)
            messages += [refusal(call, "owner_reconnect_required") for call, _, _ in owner]
            mine = [p for p in problems if p[2]["fixable_by"] != "owner"]
            payload = bundle((reason, item) for _, reason, item in mine)
            if payload is None and (self.policy.mode != "interrupt" or attempt >= recorded):
                break
            if self.policy.mode == "raise" and payload is not None:
                raise ConnectionBlockedError(payload)
            if self.policy.mode == "refuse":
                messages += [refusal(call, "auth_required") for call, _, _ in mine]
                break
            decision, attempt = ask(payload or {}, attempt, recorded)
            # Repaired since asked: the answer applies to the calls the guard watches.
            asked = [c for c, _, _ in mine] or [
                c for c in pending if c["name"] in self.policy.guarded
            ]
            if decision in ("retry", None):  # 11.5: re-read; still broken → ask again
                problems = await self._problems(asked)
                continue
            calls = asked if decision == "skip" else pending
            done |= {message.tool_call_id for message in messages}
            code = "skipped" if decision == "skip" else "cancelled"
            messages += [refusal(call, code) for call in calls if call["id"] not in done]
            jump = "end" if decision == "cancel" else None
            break
        if not messages and jump is None:
            return None
        update: dict[str, Any] = {"messages": messages}
        if jump is not None:
            update["jump_to"] = jump
        return update

    async def _problems(self, calls: Sequence[ToolCall]) -> list[Problem]:
        targets = [(c, t) for c in calls if (t := self.policy.guarded.get(c["name"])) is not None]
        if not targets:
            return []
        try:
            states = await self.policy.read_states([tool for _, tool in targets])
        except Exception:  # noqa: BLE001 - best effort: the leaf call still fails closed
            return []
        problems: list[Problem] = []
        actor = self.policy.actor_user_id
        for call, tool in targets:
            state = states.get(tool.credential_id)
            if state is None:
                continue  # unknown (no tenant, not visible): the leaf call fails closed
            if state.status not in USABLE:
                reason = "expired" if state.status == "expired" else "auth_needed"
                mine = state.user_id is not None and state.user_id == actor
                kind = "oauth" if "oauth" in state.definition_key else "api_key"
            elif tool.credential_mode == "per_user" and state.bound is False:
                reason, kind = "auth_needed", "binding"  # the account's binding is gone
                mine = tool.account_user_id is not None and tool.account_user_id == actor
            else:
                continue
            item = {
                "resource_type": tool.resource_type,
                "resource_name": tool.resource_name,
                "tool_names": [tool.name],
                "credential_id": tool.credential_id,
                "fixable_by": "me" if mine else "owner",
                "connect": {"kind": kind},
            }
            problems.append((call, reason, item))
        return problems

    # -- after the ToolNode --------------------------------------------------------
    @hook_config(can_jump_to=["end"])
    async def abefore_model(self, state: AgentState, runtime: Runtime) -> dict[str, Any] | None:
        messages = state["messages"]
        if (
            self.policy.mode == "refuse"
            or not messages
            or not isinstance(messages[-1], ToolMessage)
        ):
            return None
        _, trailing = _last_step(messages)
        asked = [(m, p) for m in trailing if (p := auth_interrupt_of(m)) is not None]
        if not asked:
            return None
        mine = [(m, p) for m, p in asked if any(i["fixable_by"] != "owner" for i in p["items"])]
        mine_ids = {id(m) for m, _ in mine}
        owner = [i for _, p in asked for i in p["items"] if i["fixable_by"] == "owner"]
        await notify_owner(owner, run=_run_key(), tenants=self._tenants)
        owner_only = [m for m, _ in asked if id(m) not in mine_ids]
        replaced = [resolved(m, "owner_reconnect_required") for m in owner_only]
        payload = bundle(
            (p["reason"], i) for _, p in mine for i in p["items"] if i["fixable_by"] != "owner"
        )
        if payload is None:
            return {"messages": replaced} if replaced else None
        if self.policy.mode == "raise":
            raise ConnectionBlockedError(payload)
        attempt, recorded = 0, recorded_answers()
        while True:
            decision, attempt = ask(payload, attempt, recorded)  # the latest recorded answer wins
            if decision is None or (decision == "retry" and await self._still_broken(payload)):
                continue  # 11.5: still not connected → the same interrupt again
            break
        code = {"retry": "reconnected", "skip": "skipped", "cancel": "cancelled"}[decision]
        update: dict[str, Any] = {"messages": [*replaced, *(resolved(m, code) for m, _ in mine)]}
        if decision == "cancel":
            update["jump_to"] = "end"
        return update

    async def _still_broken(self, payload: dict[str, Any]) -> bool:
        ids = {str(i["credential_id"]) for i in payload["items"] if i.get("credential_id")}
        tools = [t for t in self.policy.guarded.values() if t.credential_id in ids]
        if not tools:
            return False
        try:
            states = await self.policy.read_states(tools)
        except Exception:  # noqa: BLE001 - let the model try again
            return False
        current = [states.get(tool.credential_id) for tool in tools]
        return any(state is not None and state.status != "active" for state in current)

    # -- last re-check around each call ---------------------------------------------
    async def awrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], Awaitable[ToolMessage | Command]],
    ) -> ToolMessage | Command:
        call = request.tool_call
        code = self.policy.denied.get(call["name"])
        if code is None and request.tool is None:
            code = "tool_not_allowed"
        check = self.policy.checks.get(call["name"])
        if code is None and check is not None:
            allowed = await check()
            code = (
                None
                if allowed
                else ("authz_unavailable" if allowed is None else "permission_required")
            )
        if code is not None:
            return refusal(call, code)
        return await handler(request)


def with_connection_guard(
    middleware: list[Any], cfg: Any, tools: Sequence[BaseTool], *, mode: GuardMode
) -> list[Any]:
    """``middleware`` plus the run's guard while run types are enforced (else unchanged)."""

    if cfg.access_plan is None or not runtime_access_enforced():
        return middleware
    policy = build_connection_guard_policy(cfg, tools, mode=mode)
    return [*middleware, ConnectionGuardMiddleware(policy)]


__all__ = ["GUARD_NAME", "ConnectionGuardMiddleware", "with_connection_guard"]
