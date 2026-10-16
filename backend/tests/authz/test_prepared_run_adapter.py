"""Prepared-run adapter: real seeded agent rows -> complete lease inputs (Task 10)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from langchain_core.messages import AIMessage, HumanMessage
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent_runtime import prepared_run_materializer, prepared_run_participants
from app.agent_runtime.prepared_run_digest import (
    canonical_digest_bytes,
    compute_prepared_run_digest,
)
from app.agent_runtime.prepared_run_input_policy import PreparedRunUnsupportedError
from app.agent_runtime.prepared_run_registry import get_prepared_run_registry
from app.agent_runtime.runtime_policy import LEGACY_RUNTIME_POLICY
from app.authz.execution_lock_cursor import LockClass
from app.authz.organization_execution_proof import live_proof
from app.config import settings
from app.models.agent import Agent
from app.models.conversation_run import ConversationRun
from app.models.model import Model
from app.services import conversation_stream_service
from tests.authz import prepared_adapter_testkit as akit
from tests.authz import prepared_materialization_testkit as kit
from tests.conftest import TestSession

CHILD_URL = "https://bob:pw-child-secret@llm.test/v1"


def _scripts(seeded: akit.Seeded) -> dict[str, list[AIMessage]]:
    return {seeded.root_model: [AIMessage("done")], seeded.child_model: [AIMessage("c")]}


@pytest.mark.asyncio
async def test_adapter_builds_complete_inputs_from_seeded_rows_under_locks(
    db: AsyncSession, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    kit.fresh_registry(monkeypatch)
    akit.patch_keys(monkeypatch)
    seeded = await akit.seed(db, child_base_url=CHILD_URL)
    memory = akit.Memory()
    prepare = akit.resolver(seeded, tmp_path, memory)
    locked: list[bool] = []
    resolve_config = prepare._request.resolve_config  # noqa: SLF001

    async def recording(tx: AsyncSession, run: ConversationRun) -> Any:
        proof = live_proof(tx)
        locked.append(
            proof is not None
            and proof.cursor.holds(LockClass.RUN, run.id)
            and proof.cursor.holds(LockClass.AGENT_LIFECYCLE, seeded.graph.agent_id)
            and proof.cursor.holds(LockClass.AGENT_LIFECYCLE, seeded.child_agent_id)
        )
        return await resolve_config(tx, run)

    object.__setattr__(prepare._request, "resolve_config", recording)  # noqa: SLF001
    compiled: list[Any] = []
    materials: list[Any] = []
    compile_root, digest = prepared_run_materializer.compile_root, compute_prepared_run_digest

    def compile_spy(**kwargs: Any) -> Any:
        compiled.append(kwargs["interrupt_on"])
        return compile_root(**kwargs)

    def digest_spy(material: Any) -> str:
        materials.append(material)
        return digest(material)

    monkeypatch.setattr(prepared_run_materializer, "compile_root", compile_spy)
    monkeypatch.setattr(prepared_run_participants, "compute_prepared_run_digest", digest_spy)
    server = kit.FakeMcpServer()
    lease, factory = await akit.materialize(seeded, prepare, _scripts(seeded), server)

    assert locked == [True] and server.discoveries == 1
    assert memory.calls == [("policy", True), ("policy", True), ("context", True)]
    child_path = f"root/{seeded.child_name}"
    assert set(lease.binding.participant_paths) == {"root", "root/general-purpose", child_path}
    assert set(factory.built) == {seeded.root_model, seeded.child_model}
    assert lease.messages == (HumanMessage("hi"),)
    root_tools = lease.runnable.nodes["tools"].bound.tools_by_name
    assert {"lookup", "ask_user", "task"} <= set(root_tools)
    # MCP tools need approval: merged into the compiled (probe + final) HITL policy.
    assert len(compiled) == 2 and all("lookup" in policy for policy in compiled)
    assert {"sk-root", "sk-child", "pw-child-secret"} <= lease.secret_values
    (material,) = materials
    assert digest(material) == lease.digest
    encoded = canonical_digest_bytes(material)
    for secret in (b"sk-root", b"sk-child", b"pw-child-secret", b"mcp-secret"):
        assert secret not in encoded
    assert {"tool:root:mcp:lookup", f"child_launch:{child_path}"} <= material.descriptor_ids
    assert "interrupt_on" in material.snapshot_digests
    assert f"child:{seeded.child_agent_id}:updated_at" in material.versions
    assert any(ref.startswith("mcp_server:") for ref in material.secret_reference_ids)
    cfg = prepare.execution_cfg
    assert cfg is not None and cfg.api_key is None and cfg.base_url is None
    assert cfg.tools_config == [] and cfg.secret_values == set()
    assert cfg.subagent_display_names == {seeded.child_name: "Researcher"}
    assert cfg.recalled_memories
    async with TestSession() as check:
        row = await check.get(ConversationRun, seeded.run_id)
    assert row is not None and row.status == "running"
    assert row.runtime_materialization_sha256 == lease.digest
    await get_prepared_run_registry().terminalize(seeded.run_id)


@pytest.mark.asyncio
async def test_empty_first_response_is_retried_once_through_a_fresh_send(
    db: AsyncSession, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.agent_runtime.prepared_run_dispatch import handle_for, invoke_prepared

    def scripts(seeded: akit.Seeded) -> dict[str, list[AIMessage]]:
        return {seeded.root_model: [AIMessage(""), AIMessage("done")], seeded.child_model: []}

    active = await akit.activate(db, tmp_path, monkeypatch, scripts)
    result = await invoke_prepared(handle_for(active.lease))
    assert result["messages"][-1].content == "done"
    assert [active.seeded.root_model, active.seeded.root_model] == kit.SENDS


@pytest.mark.asyncio
async def test_resolver_refuses_to_run_without_gateway_locks(
    db: AsyncSession, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    akit.patch_keys(monkeypatch)
    seeded = await akit.seed(db)
    memory = akit.Memory()
    run = await db.get(ConversationRun, seeded.run_id)
    assert run is not None
    with pytest.raises(PreparedRunUnsupportedError) as error:
        await akit.resolver(seeded, tmp_path, memory)(db, run)
    assert error.value.code == "prepared_run_unsupported:inputs_unlocked"
    assert memory.calls == []


async def _middleware(db: AsyncSession, seeded: akit.Seeded) -> None:
    agent = await db.get(Agent, seeded.graph.agent_id)
    assert agent is not None
    agent.middleware_configs = [{"type": "model_retry", "params": {}}]


async def _recursion(db: AsyncSession, seeded: akit.Seeded) -> None:
    agent = await db.get(Agent, seeded.graph.agent_id)
    assert agent is not None
    agent.model_params = {"recursion_limit": 50}


async def _provider(db: AsyncSession, seeded: akit.Seeded) -> None:
    child = await db.get(Agent, seeded.child_agent_id)
    assert child is not None
    child_model = await db.get(Model, child.model_id)
    assert child_model is not None
    child_model.provider = "bedrock"


# case -> (overrides, expected reason suffix)
CASES: dict[str, tuple[dict[str, Any], str]] = {
    "middleware": ({"mutate": _middleware}, "middleware:model_retry"),
    "provider_middleware": ({"provider": "openai"}, "provider_middleware:openai"),
    "memory_tools": ({"memory": ["propose"]}, ":memory_tools"),
    "child_memory_tools": ({"memory": ["off", "propose"]}, ":child:{child}:memory_tools"),
    "recursion_limit": ({"mutate": _recursion}, "recursion_limit"),
    "fallback": ({"fallback": True}, "model_fallback"),
    "child_provider": ({"mutate": _provider}, "child:{child}:provider:bedrock"),
    "child_key": ({"child_key": None}, "child:{child}:llm_credential_missing"),
    "agent_memory_file_write": ({"policy": LEGACY_RUNTIME_POLICY}, ":agent_memory_file_write"),
}


@pytest.mark.asyncio
@pytest.mark.parametrize("case", sorted(CASES))
async def test_unsupported_shapes_fail_typed_before_marker(
    db: AsyncSession, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, case: str
) -> None:
    options, reason = CASES[case]
    registry = kit.fresh_registry(monkeypatch)
    akit.patch_keys(monkeypatch, child_key=options.get("child_key", "k"))
    if options.get("fallback"):

        async def chain(*_a: Any) -> list[dict[str, Any]]:
            return [{"provider": "anthropic", "model_name": "x", "base_url": None}]

        monkeypatch.setattr(conversation_stream_service, "resolve_fallback_chain", chain)
    seed_options = {k: options[k] for k in ("provider", "policy") if k in options}
    seeded = await akit.seed(db, **seed_options)
    if "mutate" in options:
        await options["mutate"](db, seeded)
    await db.commit()
    server = kit.FakeMcpServer()
    memory = akit.Memory(sequence=list(options.get("memory", [])))
    prepare = akit.resolver(seeded, tmp_path, memory)
    with pytest.raises(PreparedRunUnsupportedError) as error:
        await akit.materialize(seeded, prepare, _scripts(seeded), server)
    assert error.value.code.startswith("prepared_run_unsupported:")
    assert error.value.code.endswith(reason.format(child=seeded.child_name))
    assert server.discoveries == 0 and kit.SENDS == []
    assert registry.state(seeded.run_id) is None
    async with TestSession() as check:
        row = await check.get(ConversationRun, seeded.run_id)
    assert row is not None and row.status == "queued" and row.runtime_materialized_at is None


def test_prepared_run_lease_gate_defaults_off() -> None:
    assert settings.prepared_run_lease_enabled is False
