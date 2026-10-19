"""Reusable authority-denial terminalization (parent Task 10 I14; Task 13 consumer seam).

``terminalize_authority_denial`` applies one frozen projection for both
``organization_archived`` and the future ``tenant_suspended`` reason to exact,
already-locked rows:

* unclaimed pending input -> ``canceled``, ``run_id`` stays NULL, revision + 1;
* claimed input -> stays bound, ``failed``;
* accepted run without the materialization pair -> ``failed``, inactive,
  ``completed_at`` set, exact ``error_code``/``error_message``;
* linked API run -> ``failed``, finish time, exact code/message, link retained;
* linked trigger run -> ``failed``, finish time, ``error_message=reason``.

A run that already carries the materialization pair belongs to a live lease and
is left to finish under the closed contract. No trigger/deployment
administrative row is touched and nothing is replayed or re-enabled.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal
from uuid import UUID

from app.models.agent_api import AgentApiRun
from app.models.agent_trigger_run import AgentTriggerRun
from app.models.conversation_run import ConversationRun
from app.models.conversation_run_input import ConversationRunInput

DenialReason = Literal["organization_archived", "tenant_suspended"]
_MESSAGES: dict[str, str] = {
    "organization_archived": "조직이 보관되어 실행이 종료되었습니다",
    "tenant_suspended": "회사가 정지되어 실행이 종료되었습니다",
}


@dataclass(frozen=True, slots=True)
class DenialOutcome:
    canceled_inputs: int = 0
    failed_inputs: int = 0
    failed_runs: int = 0
    lease_owned_runs: int = 0
    failed_api_runs: int = 0
    failed_trigger_runs: int = 0
    # Runs failed here: their in-flight local tasks must be stopped post-commit (I24 H2).
    failed_run_ids: tuple[UUID, ...] = ()


def _now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


_RUN_TERMINAL = frozenset({"completed", "failed", "canceled", "stale"})
_API_TERMINAL = frozenset({"succeeded", "failed", "canceled"})
_TRIGGER_TERMINAL = frozenset({"success", "failed", "canceled", "skipped"})


def terminalize_authority_denial(
    reason: DenialReason,
    *,
    inputs: Sequence[ConversationRunInput] = (),
    runs: Sequence[ConversationRun] = (),
    api_runs: Sequence[AgentApiRun] = (),
    trigger_runs: Sequence[AgentTriggerRun] = (),
) -> DenialOutcome:
    """Mutate the given locked rows in place (caller flushes inside its transaction).

    Idempotent: terminal rows are skipped, so a repeated call changes nothing.
    Runs are decided first; a claimed input and a linked envelope fail only when
    their own run failed here (lease-owned or already-terminal runs keep theirs).
    Unlinked envelopes are out of scope (legacy-conflict class).
    """

    message = _MESSAGES[reason]
    now = _now()
    failed_runs = lease_owned = 0
    failed_run_ids: dict[UUID, None] = {}
    for run in runs:
        if run.status in _RUN_TERMINAL:
            continue
        if run.runtime_materialized_at is not None:
            lease_owned += 1
            continue
        run.status = "failed"
        run.is_active = False
        run.completed_at = run.completed_at or now
        run.error_code = reason
        run.error_message = message
        failed_runs += 1
        failed_run_ids[run.id] = None
    canceled = failed_inputs = 0
    for item in inputs:
        if item.status == "pending" and item.run_id is None:
            item.status = "canceled"
            item.revision += 1
            canceled += 1
        elif item.status in {"pending", "claimed"} and item.run_id in failed_run_ids:
            item.status = "failed"
            failed_inputs += 1
    api_failed = 0
    for api_run in api_runs:
        if api_run.conversation_run_id not in failed_run_ids or api_run.status in _API_TERMINAL:
            continue
        api_run.status, api_run.finished_at = "failed", now
        api_run.error_code, api_run.error_message = reason, message
        api_failed += 1
    trigger_failed = 0
    for trigger_run in trigger_runs:
        if (
            trigger_run.conversation_run_id not in failed_run_ids
            or trigger_run.status in _TRIGGER_TERMINAL
        ):
            continue
        trigger_run.status, trigger_run.finished_at = "failed", now
        trigger_run.error_message = reason
        trigger_failed += 1
    return DenialOutcome(
        canceled,
        failed_inputs,
        failed_runs,
        lease_owned,
        api_failed,
        trigger_failed,
        tuple(failed_run_ids),
    )
