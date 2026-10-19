"""I24 H3/M1/M2 for the Agent API prepared entry.

* H3: the default run root (no seam override) lives outside the runtime data root,
  so the lease backend accepts it; a forbidden root fails with the machine code
  only (no physical path in the raised text, run row or envelope).
* M1: the run root is removed after terminalization.
* M2: a mid-run dispatch denial is the run's ``error_code`` and the linked
  envelope inherits it.
"""

from __future__ import annotations

import shutil
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest
from langchain_core.messages import AIMessage
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent_api import prepared_execution, run_execution, runtime_service
from app.agent_runtime.lease_dispatch_permit import DispatchDeniedError
from app.agent_runtime.runtime_config import runtime_data_dir
from app.models.agent_api import AgentApiRun
from app.services import prepared_conversation_executor
from tests.authz import prepared_agent_api_testkit as apikit
from tests.authz import prepared_materialization_testkit as kit
from tests.conftest import TestSession


def _root_base(monkeypatch: pytest.MonkeyPatch, base: Path | None) -> None:
    monkeypatch.setattr(
        prepared_execution, "SEAMS", replace(prepared_execution.SEAMS, run_root_base=base)
    )


async def _run(api: apikit.ApiRun, mode: str) -> str:
    if mode == "wait":
        return await runtime_service.execute_owned_wait(api.execution)
    return "".join([chunk async for chunk in runtime_service.stream_owned_run(api.execution)])


async def _fail_envelope(api: apikit.ApiRun, message: str) -> AgentApiRun:
    """What the router does after an execution error (``AGENT_API_RUN_FAILED``)."""

    async with TestSession() as db:
        (envelope,) = (await apikit.rows(api)).envelopes
        row = await db.get(AgentApiRun, envelope.id)
        assert row is not None
        return await run_execution.mark_run_failed(
            db, row, code="AGENT_API_RUN_FAILED", message=message
        )


@pytest.mark.asyncio
async def test_default_run_root_is_outside_the_data_root_and_removed(
    db: AsyncSession, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    apikit.enable(monkeypatch)
    api = await apikit.seed_api_run(db, tmp_path, monkeypatch)
    apikit.forbid_legacy(monkeypatch)
    _root_base(monkeypatch, None)  # production default seam

    assert await runtime_service.execute_owned_wait(api.execution) == "done"

    state = await apikit.rows(api)
    assert state.run.status == "completed", (state.run.error_code, state.run.error_message)
    root = prepared_conversation_executor.prepared_run_root()
    assert not root.resolve().is_relative_to(runtime_data_dir().resolve())
    assert not (root / str(api.execution.conversation_run_id)).exists()


@pytest.mark.asyncio
async def test_forbidden_run_root_fails_with_code_and_never_a_physical_path(
    db: AsyncSession, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    apikit.enable(monkeypatch)
    api = await apikit.seed_api_run(db, tmp_path, monkeypatch)
    apikit.forbid_legacy(monkeypatch)
    shared = runtime_data_dir() / f"i24-shared-{api.execution.conversation_run_id}"
    _root_base(monkeypatch, shared)
    try:
        with pytest.raises(Exception) as failed:  # noqa: PT011 - the coded opaque error
            await runtime_service.execute_owned_wait(api.execution)
        envelope = await _fail_envelope(api, str(failed.value))
    finally:
        shutil.rmtree(shared, ignore_errors=True)

    assert getattr(failed.value, "code", None) == "lease_backend_root_shared"
    state = await apikit.rows(api)
    texts = [str(failed.value), state.run.error_code, state.run.error_message]
    texts += [envelope.error_code, envelope.error_message]
    assert all(str(shared) not in str(text) and "/" not in str(text) for text in texts)
    assert kit.SENDS == [] and state.run.runtime_materialized_at is None


class _Denied(kit.GuardedScriptedModel):
    async def _agenerate(self, *args: Any, **kwargs: Any) -> Any:
        raise DispatchDeniedError("organization_archived")


class _DenyingFactory(kit.ScriptedFactory):
    def __call__(self, spec: Any, guard: Any) -> kit.FakeOwned:
        owned = super().__call__(spec, guard)
        if spec.model_name.startswith("root-"):
            model = _Denied(responses=[AIMessage("never")])
            model._guard, model._tag = guard, spec.model_name
            owned = self.built[spec.model_name] = kit.FakeOwned(model)
        return owned


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["wait", "stream"])
async def test_mid_run_denial_is_the_run_code_and_the_envelope_inherits_it(
    db: AsyncSession, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mode: str
) -> None:
    apikit.enable(monkeypatch)
    api = await apikit.seed_api_run(db, tmp_path, monkeypatch, mode=mode, factory=_DenyingFactory)
    apikit.forbid_legacy(monkeypatch)

    with pytest.raises(Exception) as denied:  # noqa: PT011 - wait raises it, stream wraps it
        await _run(api, mode)

    state = await apikit.rows(api)
    assert (state.run.status, state.run.error_code) == ("failed", "organization_archived")
    envelope = await _fail_envelope(api, str(denied.value))
    assert envelope.error_code == "organization_archived"
    assert not (tmp_path / "prepared-api" / str(api.execution.conversation_run_id)).exists()
