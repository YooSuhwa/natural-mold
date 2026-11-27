"""Recovery may use only an intact record of the sends a previous worker reserved."""

from __future__ import annotations

import json
from dataclasses import replace
from typing import Any
from uuid import UUID, uuid4

import pytest
from langgraph.runtime import ExecutionInfo

from app.agent_runtime.stream_retry_authority import ModelAttempt
from app.chat_recovery.identity import recovery_runtime_digest
from app.chat_recovery.model_attempts import (
    RecoveryModelAttemptBlocked,
    new_model_attempt_budget,
    reserve_model_attempt,
)
from app.config import settings
from app.models.conversation_run import ConversationRun
from tests.agent_runtime.test_chat_recovery_identity import _config


def _attempt(conversation: UUID, *, limit: int = 3) -> ModelAttempt:
    task = str(uuid4())
    info = ExecutionInfo(str(uuid4()), f"model:{task}", task, str(conversation))
    return ModelAttempt(info, limit)


@pytest.mark.parametrize(
    "corrupt",
    [
        {"limit": 3, "calls": {}},
        {"version": True, "limit": 3, "calls": {}},
        {"version": 1, "limit": 999, "calls": {}},
        {"version": 1, "limit": 3, "calls": {"malformed": 2}},
        {"version": 1, "limit": 3, "calls": {"a" * 64: False}},
        {"version": 1, "limit": 3, "calls": {}, "local_generation": None},
    ],
)
def test_malformed_budget_stops_before_reservation(corrupt: dict[str, Any]) -> None:
    run = ConversationRun(
        conversation_id=uuid4(), owner_generation=1, recovery_model_attempts=corrupt
    )
    with pytest.raises(RecoveryModelAttemptBlocked, match="recovery_model_attempt_unproven"):
        reserve_model_attempt(run, _attempt(run.conversation_id))
    assert run.recovery_model_attempts is corrupt


def test_caller_cannot_increase_the_original_limit_or_change_conversations() -> None:
    run = ConversationRun(
        conversation_id=uuid4(),
        owner_generation=1,
        recovery_model_attempts=new_model_attempt_budget(1),
    )
    first = _attempt(run.conversation_id)
    assert reserve_model_attempt(run, first)
    run.owner_generation = 2
    with pytest.raises(RecoveryModelAttemptBlocked, match="recovery_model_attempt_limit"):
        reserve_model_attempt(run, replace(first, limit=3))
    for attempt in (_attempt(uuid4()), ModelAttempt(None, 3)):
        with pytest.raises(RecoveryModelAttemptBlocked, match="recovery_model_attempt_unproven"):
            reserve_model_attempt(run, attempt)


def test_bounded_record_allows_long_live_run_and_closes_displaced_generation() -> None:
    run = ConversationRun(
        conversation_id=uuid4(),
        owner_generation=1,
        recovery_model_attempts=new_model_attempt_budget(1),
    )
    initial = _attempt(run.conversation_id)
    assert reserve_model_attempt(run, initial)
    for _ in range(255):
        assert reserve_model_attempt(run, _attempt(run.conversation_id))
    overflow = _attempt(run.conversation_id)
    assert reserve_model_attempt(run, overflow)  # Saves the owning generation before any send.
    assert not reserve_model_attempt(run, _attempt(run.conversation_id))
    assert len(json.dumps(run.recovery_model_attempts)) < 20 * 1024
    with pytest.raises(RecoveryModelAttemptBlocked, match="recovery_model_attempt_limit"):
        reserve_model_attempt(run, initial)
    run.owner_generation = 2
    with pytest.raises(RecoveryModelAttemptBlocked, match="recovery_model_attempt_unproven"):
        reserve_model_attempt(run, overflow)


def test_recovery_pins_stream_retry_opt_in_for_the_selected_model(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cfg = _config()
    monkeypatch.setattr(settings, "chat_stream_retry_providers", set())
    ordinary_digest = recovery_runtime_digest(cfg)
    assert ordinary_digest is not None
    monkeypatch.setattr(settings, "chat_stream_retry_providers", {cfg.provider})
    opted_in_digest = recovery_runtime_digest(cfg)
    assert opted_in_digest and opted_in_digest != ordinary_digest
    monkeypatch.setattr(settings, "chat_stream_retry_providers", {"unrelated"})
    assert recovery_runtime_digest(cfg) == ordinary_digest
