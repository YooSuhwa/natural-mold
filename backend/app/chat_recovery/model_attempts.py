"""Reserve sends for a logical model task under the run's locked owner generation."""

from __future__ import annotations

import hashlib
import re
from enum import StrEnum
from typing import Any
from uuid import UUID

from app.agent_runtime.stream_retry_authority import ModelAttempt
from app.models.conversation_run import ConversationRun

# At most 256 hashes and small counters (under 20 KiB) on a private run. A long
# running worker keeps its local policy; if the record filled, a successor stops.
_MAX_TRACKED_TASKS = 256


class AttemptStop(StrEnum):
    LIMIT = "recovery_model_attempt_limit"
    UNPROVEN = "recovery_model_attempt_unproven"


class RecoveryModelAttemptBlocked(RuntimeError):
    def __init__(self, code: AttemptStop) -> None:
        self.code = code.value
        super().__init__(self.code)


def new_model_attempt_budget(limit: int) -> dict[str, Any]:
    if type(limit) is not int or not 1 <= limit <= 3:
        raise ValueError("recoverable model attempt limit must be between one and three")
    return {"version": 1, "limit": limit, "calls": {}}


def _task_key(attempt: ModelAttempt, conversation_id: UUID) -> str:
    info = attempt.execution
    if info is None or info.thread_id != str(conversation_id):
        raise RecoveryModelAttemptBlocked(AttemptStop.UNPROVEN)
    try:
        checkpoint = UUID(info.checkpoint_id)
        task = UUID(info.task_id)
    except (AttributeError, TypeError, ValueError) as exc:
        raise RecoveryModelAttemptBlocked(AttemptStop.UNPROVEN) from exc
    # Both values come from LangGraph's ExecutionInfo. The task ID includes its
    # graph namespace. An exact checkpoint continuation reuses both UUIDs.
    return hashlib.sha256(checkpoint.bytes + task.bytes).hexdigest()


def reserve_model_attempt(run: ConversationRun, attempt: ModelAttempt) -> bool:
    """Update the locked row before dispatch. True means commit the new reservation."""
    budget = run.recovery_model_attempts
    if budget is None:
        # An in-flight legacy worker can finish the original invocation with its
        # local limit. A replacement cannot infer a prior process's send count.
        if run.owner_generation == 1:
            return False
        raise RecoveryModelAttemptBlocked(AttemptStop.UNPROVEN)
    if (
        not isinstance(budget, dict)
        or type(budget.get("version")) is not int
        or budget["version"] != 1
    ):
        raise RecoveryModelAttemptBlocked(AttemptStop.UNPROVEN)
    limit = budget.get("limit")
    calls = budget.get("calls")
    local_generation = budget.get("local_generation")
    if (
        type(limit) is not int
        or not 1 <= limit <= 3
        or type(attempt.limit) is not int
        or not 1 <= attempt.limit <= 3
        or not isinstance(calls, dict)
        or len(calls) > _MAX_TRACKED_TASKS
        or any(
            not isinstance(key, str)
            or not re.fullmatch("[0-9a-f]{64}", key)
            or type(value) is not int
            or not 1 <= value <= limit
            for key, value in calls.items()
        )
        or (
            "local_generation" in budget
            and (type(local_generation) is not int or local_generation != run.owner_generation)
        )
    ):
        raise RecoveryModelAttemptBlocked(AttemptStop.UNPROVEN)

    key = _task_key(attempt, run.conversation_id)
    used = calls.get(key, 0)
    if used >= min(limit, attempt.limit):
        raise RecoveryModelAttemptBlocked(AttemptStop.LIMIT)
    if key not in calls and len(calls) == _MAX_TRACKED_TASKS:
        if local_generation is None:
            # This previously unseen task has used zero sends; the current
            # middleware can bound it locally. No further generation may send.
            run.recovery_model_attempts = {**budget, "local_generation": run.owner_generation}
            return True
        return False
    run.recovery_model_attempts = {**budget, "calls": {**calls, key: used + 1}}
    return True
