"""Background queue dispatch treats a no-longer-executable conversation as nothing to do."""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import Any
from uuid import uuid4

import pytest
from fastapi import HTTPException

from app.database import SessionTenant
from app.services import conversation_run_queue_worker as worker


def _install(monkeypatch: pytest.MonkeyPatch, error: HTTPException) -> list[Any]:
    launched: list[Any] = []

    async def company_of(_query: Any) -> SessionTenant:
        return SessionTenant(uuid4(), uuid4())

    @asynccontextmanager
    async def company_session(_scope: SessionTenant):  # type: ignore[no-untyped-def]
        yield object()

    async def actor(*_args: Any) -> object:
        return object()

    async def claim(*_args: Any, **_kwargs: Any) -> None:
        raise error

    async def launch(input_id: Any) -> None:
        launched.append(input_id)

    monkeypatch.setattr(worker, "_company_of", company_of)
    monkeypatch.setattr(worker, "_company_session", company_session)
    monkeypatch.setattr(worker, "resolve_conversation_actor", actor)
    monkeypatch.setattr(worker.conversation_run_queue_service, "claim_next_input", claim)
    monkeypatch.setattr(worker, "launch_claimed_input", launch)
    return launched


async def test_archived_conversation_dispatches_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    # Given: the claim refuses the conversation with the queue's opaque 404 (org archived).
    launched = _install(monkeypatch, HTTPException(status_code=404, detail="gone"))
    # When: a finished run's teardown dispatches the next queued input.
    # Then: nothing is launched and the teardown does not raise.
    assert await worker.dispatch_next_for_conversation(uuid4()) is None
    assert launched == []


async def test_other_claim_errors_still_propagate(monkeypatch: pytest.MonkeyPatch) -> None:
    _install(monkeypatch, HTTPException(status_code=409, detail="conflict"))
    with pytest.raises(HTTPException) as error:
        await worker.dispatch_next_for_conversation(uuid4())
    assert error.value.status_code == 409
