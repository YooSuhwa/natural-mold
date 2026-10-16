"""I17: accepted Agent API run -> gateway materialize -> exact lease dispatch -> terminal."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from langchain_core.messages import AIMessage
from sqlalchemy import delete, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent_api import prepared_execution, run_execution, runtime_service
from app.agent_runtime import prepared_run_participants
from app.agent_runtime.lease_dispatch_permit import DispatchDeniedError
from app.agent_runtime.prepared_run_digest import compute_prepared_run_digest
from app.agent_runtime.prepared_run_registry import (
    LEASE_MISSING,
    PreparedRunLeaseError,
    get_prepared_run_registry,
)
from app.agent_runtime.prepared_run_transaction import (
    RUN_NOT_MATERIALIZABLE,
    PreparedRunTransactionError,
)
from app.agent_runtime.runtime_hydration_guard import active_lease_run
from app.models.agent_api import AgentApiKey
from app.models.organization import Organization, OrganizationMember
from tests.authz import prepared_agent_api_testkit as apikit
from tests.authz import prepared_materialization_testkit as kit
from tests.authz.test_prepared_runtime_installed_compiler import PostMarkerSpies
from tests.authz.test_prepared_subagent_dispatch import _RevokingModel
from tests.conftest import TestSession


def _spy_deployment_reads(monkeypatch: pytest.MonkeyPatch) -> list[str | None]:
    """Record the active lease run (``None`` = pre-marker) at each deployment read."""

    seen: list[str | None] = []
    original = run_execution.authorize_deployment

    async def spy(*args: Any, **kwargs: Any) -> Any:
        seen.append(active_lease_run())
        return await original(*args, **kwargs)

    monkeypatch.setattr(run_execution, "authorize_deployment", spy)
    return seen


async def _run(api: apikit.ApiRun, mode: str) -> str:
    if mode == "wait":
        return await runtime_service.execute_owned_wait(api.execution)
    chunks = [chunk async for chunk in runtime_service.stream_owned_run(api.execution)]
    return "".join(chunks)


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["wait", "stream"])
async def test_prepared_api_run_dispatches_exact_lease_without_post_marker_hydration(
    db: AsyncSession, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mode: str
) -> None:
    apikit.enable(monkeypatch)
    api = await apikit.seed_api_run(db, tmp_path, monkeypatch, mode=mode)
    apikit.forbid_legacy(monkeypatch)
    reads = _spy_deployment_reads(monkeypatch)
    materials: list[Any] = []

    def digest_spy(material: Any) -> str:
        materials.append(material)
        return compute_prepared_run_digest(material)

    monkeypatch.setattr(prepared_run_participants, "compute_prepared_run_digest", digest_spy)
    spies: list[PostMarkerSpies] = []
    original = prepared_execution.materialize_api_run

    async def then_spy(*args: Any, **kwargs: Any) -> Any:
        prepared = await original(*args, **kwargs)
        assert kit.SENDS == []
        spies.append(PostMarkerSpies(monkeypatch))
        return prepared

    monkeypatch.setattr(prepared_execution, "materialize_api_run", then_spy)

    output = await _run(api, mode)

    assert "done" in output
    assert [api.seeded.root_model] == kit.SENDS
    assert reads == [None] and len(spies) == 1 and spies[0].compiled == []
    assert api.server.discoveries == 1 and set(api.factory.closes().values()) == {1}
    (material,) = materials
    assert material.versions["api_public_run_id"] == api.execution.public_run_id
    assert material.versions["api_key"] == str(api.key.id)
    state = await apikit.rows(api)
    assert state.run.status == "completed"
    assert state.run.runtime_materialization_sha256 == compute_prepared_run_digest(material)
    assert [envelope.status for envelope in state.envelopes] == ["succeeded"]
    assert get_prepared_run_registry().state(api.execution.conversation_run_id) is None


@pytest.mark.asyncio
async def test_replay_of_accepted_run_keeps_the_same_envelope_without_rebuild(
    db: AsyncSession, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    apikit.enable(monkeypatch)
    api = await apikit.seed_api_run(db, tmp_path, monkeypatch)
    apikit.forbid_legacy(monkeypatch)
    assert await runtime_service.execute_owned_wait(api.execution) == "done"
    first = await apikit.rows(api)

    with pytest.raises(PreparedRunTransactionError) as replay:
        await runtime_service.execute_owned_wait(api.execution)

    assert replay.value.code == RUN_NOT_MATERIALIZABLE
    assert [api.seeded.root_model] == kit.SENDS and api.server.discoveries == 1
    again = await apikit.rows(api)
    assert [e.id for e in again.envelopes] == [e.id for e in first.envelopes]
    assert [e.public_id for e in again.envelopes] == [api.execution.public_run_id]
    assert again.run.status == "completed" and again.envelopes[0].status == "succeeded"
    assert again.run.runtime_materialization_sha256 == first.run.runtime_materialization_sha256


@pytest.mark.asyncio
async def test_membership_removal_mid_run_denies_the_next_leaf(
    db: AsyncSession, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    holder: dict[str, Any] = {}

    async def revoke() -> None:
        ctx = holder["api"].seeded.graph.context
        async with TestSession() as tx:
            await tx.execute(
                delete(OrganizationMember).where(
                    OrganizationMember.org_id == ctx.org_id,
                    OrganizationMember.user_id == ctx.user_id,
                )
            )
            await tx.commit()

    class Factory(kit.ScriptedFactory):
        def __call__(self, spec: Any, guard: Any) -> kit.FakeOwned:
            owned = super().__call__(spec, guard)
            if spec.model_name.startswith("root-"):
                model = _RevokingModel(responses=list(self.scripts[spec.model_name]))
                model._guard, model._tag, model._revoke = guard, spec.model_name, revoke
                owned = self.built[spec.model_name] = kit.FakeOwned(model)
            return owned

    apikit.enable(monkeypatch)
    lookup = AIMessage("", tool_calls=[{"name": "lookup", "args": {}, "id": "call-l"}])

    def scripts(seeded: Any) -> dict[str, list[AIMessage]]:
        return {seeded.root_model: [lookup, AIMessage("done")]}

    api = await apikit.seed_api_run(db, tmp_path, monkeypatch, scripts, factory=Factory)
    holder["api"] = api
    apikit.forbid_legacy(monkeypatch)

    with pytest.raises(DispatchDeniedError) as denied:
        await runtime_service.execute_owned_wait(api.execution)

    assert denied.value.code == "forbidden"
    assert [api.seeded.root_model] == kit.SENDS and api.server.calls == 0
    state = await apikit.rows(api)
    assert state.run.status == "failed" and state.envelopes[0].status == "failed"
    assert get_prepared_run_registry().state(api.execution.conversation_run_id) is None


@pytest.mark.asyncio
async def test_registry_loss_terminalizes_without_rebuild(
    db: AsyncSession, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    apikit.enable(monkeypatch)
    api = await apikit.seed_api_run(db, tmp_path, monkeypatch)
    apikit.forbid_legacy(monkeypatch)
    original = prepared_execution.materialize_api_run

    async def lose_lease(*args: Any, **kwargs: Any) -> Any:
        prepared = await original(*args, **kwargs)
        await get_prepared_run_registry().terminalize(api.execution.conversation_run_id)
        return prepared

    monkeypatch.setattr(prepared_execution, "materialize_api_run", lose_lease)

    with pytest.raises(PreparedRunLeaseError) as missing:
        await runtime_service.execute_owned_wait(api.execution)

    assert missing.value.code == LEASE_MISSING
    assert kit.SENDS == [] and api.server.discoveries == 1
    state = await apikit.rows(api)
    assert state.run.status == "failed" and state.run.error_code == LEASE_MISSING
    assert [envelope.status for envelope in state.envelopes] == ["failed"]


async def _revoke_key(db: AsyncSession, api: apikit.ApiRun) -> None:
    await db.execute(
        update(AgentApiKey).where(AgentApiKey.id == api.key.id).values(revoked_at=datetime.now())
    )


async def _remove_member(db: AsyncSession, api: apikit.ApiRun) -> None:
    ctx = api.seeded.graph.context
    await db.execute(
        delete(OrganizationMember).where(
            OrganizationMember.org_id == ctx.org_id, OrganizationMember.user_id == ctx.user_id
        )
    )


async def _archive_org(db: AsyncSession, api: apikit.ApiRun) -> None:
    await db.execute(
        update(Organization)
        .where(Organization.id == api.seeded.graph.context.org_id)
        .values(status="archived", archived_at=datetime.now(UTC))
    )


async def _archive_and_remove(db: AsyncSession, api: apikit.ApiRun) -> None:
    await _archive_org(db, api)
    await _remove_member(db, api)


DENIALS = {
    "revoked_key": _revoke_key,
    "removed_member": _remove_member,
    "archived_org": _archive_org,
    "archived_org_removed_member": _archive_and_remove,
}


@pytest.mark.asyncio
@pytest.mark.parametrize("case", sorted(DENIALS))
async def test_revocation_before_marker_is_opaque_with_zero_provider_calls(
    db: AsyncSession, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, case: str
) -> None:
    apikit.enable(monkeypatch)
    api = await apikit.seed_api_run(db, tmp_path, monkeypatch)
    apikit.forbid_legacy(monkeypatch)
    await DENIALS[case](db, api)
    await db.commit()

    with pytest.raises(Exception) as denied:  # noqa: PT011 - each case has its own opaque error
        await runtime_service.execute_owned_wait(api.execution)

    gateway_denied = case != "revoked_key"
    expected = RUN_NOT_MATERIALIZABLE if gateway_denied else "AGENT_API_KEY_REVOKED"
    assert getattr(denied.value, "code", None) == expected
    assert "sk-root" not in str(denied.value)
    assert kit.SENDS == [] and api.server.discoveries == 0
    state = await apikit.rows(api)
    assert state.run.runtime_materialized_at is None
    # The gateway cannot lock a removed/archived owner's run: it stays unmarked and queued.
    assert state.run.status == ("queued" if gateway_denied else "failed")
    assert state.run.error_code == (None if gateway_denied else "AGENT_API_KEY_REVOKED")
    assert all(envelope.status != "succeeded" for envelope in state.envelopes)
    assert get_prepared_run_registry().state(api.execution.conversation_run_id) is None
