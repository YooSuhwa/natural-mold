"""Publish run-scoped outcomes without exposing graph state or runtime exceptions."""

from __future__ import annotations

from collections.abc import Callable, Collection, Mapping, Sequence

from ag_ui.core import Interrupt, RunErrorEvent, RunFinishedEvent
from ag_ui.core.events import (
    RunFinishedCancelledOutcome,
    RunFinishedInterruptOutcome,
    RunFinishedSuccessOutcome,
    TokenUsage,
)

from app.agent_runtime.ag_ui_v1_interrupt_payload import interrupt_fields
from app.agent_runtime.protocol_events import (
    ProtocolInterrupt,
    StoredProtocolEvent,
    protocol_interrupts_from_event,
)
from app.agent_runtime.run_error_codes import DENIAL_RUN_ERROR_CODES

# Only stable application codes can cross the public boundary. Provider/graph errors can
# contain request bodies, credentials and locations even when they look like short codes.
_PUBLIC_ERRORS = frozenset(DENIAL_RUN_ERROR_CODES.values()) | {
    "run_failed",
    "runtime_error",
    "stream_error",
    "worker_lost",
    "worker_shutdown",
    "prepared_run_start_failed",
    "pending_input_unavailable",
    "partial_model_stream",
    "recovery_effect_unconfirmed",
    "recovery_authority_revoked",
    "recovery_policy_changed",
    "recovery_limit_reached",
    "recovery_launch_failed",
    "tool_result_unavailable",
}


def public_interrupt(
    interrupt: ProtocolInterrupt,
    *,
    subagent_run_id: str | None = None,
    expires_at: str | None = None,
) -> Interrupt:
    """Shape an authorized, secret-redacted checkpoint interrupt for a public client."""
    fields = interrupt_fields(interrupt["value"])
    return Interrupt(
        id=interrupt["id"],
        subagent_run_id=subagent_run_id,
        expires_at=expires_at,
        **fields,
    )


def _auth_identity(item: Interrupt) -> tuple[str, int] | None:
    if item.reason != "moldy.auth_required":
        return None
    value = (item.metadata or {}).get("moldy")
    if not isinstance(value, Mapping):
        return None
    group = value.get("auth_request_id")
    if not isinstance(group, str) or not group or group == "<redacted>":
        group = item.id  # The first interrupt id is also the old checkpoint's group id.
    attempt = value.get("attempt")
    return group, attempt if type(attempt) is int else 1


class PendingInterrupts:
    """Current publicly visible slots; an auth re-ask replaces only its own attempt."""

    def __init__(self) -> None:
        self._by_id: dict[str, Interrupt] = {}
        self._conflicting_ids = False

    def add(
        self,
        interrupt: ProtocolInterrupt,
        *,
        subagent_run_id: str | None,
        expires_at: str | None = None,
    ) -> None:
        prior = self._by_id.get(interrupt["id"])
        if prior and prior.subagent_run_id:
            if subagent_run_id and prior.subagent_run_id != subagent_run_id:
                # A resume cannot disambiguate two owners with the same wire id.
                self._conflicting_ids = True
                return
            subagent_run_id = prior.subagent_run_id
        item = public_interrupt(interrupt, subagent_run_id=subagent_run_id, expires_at=expires_at)
        auth = _auth_identity(item)
        if auth:
            for previous in tuple(self._by_id.values()):
                if previous.subagent_run_id != item.subagent_run_id:
                    continue
                previous_auth = _auth_identity(previous)
                if previous_auth is None or previous_auth[0] != auth[0]:
                    continue
                if previous_auth[1] > auth[1]:
                    return  # Replayed stale slot, including an earlier attempt at the same ID.
                if previous.id != item.id:
                    if previous_auth[1] == auth[1]:
                        self._conflicting_ids = True
                        return
                    del self._by_id[previous.id]
        self._by_id[item.id] = item

    def observe(self, event: StoredProtocolEvent, *, subagent_run_id: str | None) -> None:
        for interrupt in protocol_interrupts_from_event(event):
            self.add(interrupt, subagent_run_id=subagent_run_id)

    def replace_current(
        self,
        interrupts: Sequence[ProtocolInterrupt],
        *,
        resolve_owner: Callable[[tuple[str, ...]], str | None],
        expires_at: Mapping[str, str] | None = None,
        legacy_child_ids: Collection[str] = (),
        passive: bool = False,
    ) -> None:
        """Replace journal slots with the caller's authorized current checkpoint slots."""
        prior = self._by_id
        self._by_id = {}
        self._conflicting_ids = False
        for interrupt in interrupts:
            namespace = tuple(interrupt["ns"])
            owner = resolve_owner(namespace)
            if owner is None:
                if namespace:
                    # The reader has not supplied a public identity for this child.
                    continue
                # Some LangGraph checkpoint serializers omit namespaces even when the
                # journal attributed this very same wire slot to an authorized child.
                previous = prior.get(interrupt["id"])
                if previous:
                    owner = previous.subagent_run_id
            self.add(
                interrupt,
                subagent_run_id=owner,
                expires_at=expires_at.get(interrupt["id"]) if expires_at else None,
            )

        if (
            passive
            or set(self._by_id) != {item["id"] for item in interrupts}
            or any(not item.response_schema for item in self._by_id.values())
        ):
            self.mark_passive()
        elif legacy_child_ids:
            for ident in legacy_child_ids:
                item = self._by_id.get(ident)
                if item and item.subagent_run_id and item.response_schema:
                    self._set_access(item, passive=False)

    def _set_access(self, item: Interrupt, *, passive: bool) -> None:
        metadata = dict(item.metadata or {})
        current = metadata.get("moldy")
        fields = dict(current) if isinstance(current, Mapping) else {}
        if passive:
            fields.pop("legacyInterruptAccess", None)
            fields["actionable"] = False
        else:
            fields["legacyInterruptAccess"] = True
        metadata["moldy"] = fields
        self._by_id[item.id] = item.model_copy(
            update={
                "metadata": metadata,
                "response_schema": None if passive else item.response_schema,
            }
        )

    def mark_passive(self) -> None:
        """Show authorized slot descriptions when a complete resume is unavailable."""
        for item in tuple(self._by_id.values()):
            self._set_access(item, passive=True)

    def public(self) -> list[Interrupt]:
        return [] if self._conflicting_ids else list(self._by_id.values())

    def for_owner(self, owner: str | None) -> list[str]:
        return [item.id for item in self.public() if item.subagent_run_id == owner]


def run_terminal_event(
    thread_id: str,
    run_id: str,
    status: str,
    pending: PendingInterrupts,
    *,
    error_code: str | None = None,
    usage: list[TokenUsage] | None = None,
) -> RunFinishedEvent | RunErrorEvent:
    """Translate a confirmed terminal state; disconnect and active states cannot finish a run."""
    outcome: RunFinishedSuccessOutcome | RunFinishedInterruptOutcome | RunFinishedCancelledOutcome
    if status == "completed":
        outcome = RunFinishedSuccessOutcome()
    elif status in {"canceled", "cancelled"}:
        outcome = RunFinishedCancelledOutcome()
    elif status == "interrupted" and (interrupts := pending.public()):
        outcome = RunFinishedInterruptOutcome(interrupts=interrupts)
    elif status in {"interrupted", "failed", "stale"}:
        if status == "interrupted":
            code = "pending_input_unavailable"
        elif error_code is not None and error_code in _PUBLIC_ERRORS:
            code = error_code
        else:
            code = "worker_lost" if status == "stale" else "run_failed"
        messages = {
            "pending_input_unavailable": "The pending request could not be restored.",
            "recovery_effect_unconfirmed": "Confirm whether the previous action took effect.",
            "partial_model_stream": "The model response stopped after partial output.",
            "worker_lost": "The worker stopped before this run could finish.",
        }
        return RunErrorEvent(
            message=messages.get(code, "The run could not finish."), code=code, usage=usage
        )
    else:
        raise ValueError("A confirmed terminal run status is required.")
    return RunFinishedEvent(thread_id=thread_id, run_id=run_id, outcome=outcome, usage=usage)
