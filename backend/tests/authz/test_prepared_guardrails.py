"""Guardrails on the prepared-lease path (guardrail-minimum-plan.md P1, completion item 7).

The lease compiles the guardrail middleware and tool risk actions into the root and every
child, records them in the lease digest, and still falls back for approval rules (whose
``interrupt_on`` predicates frozen inputs cannot hold).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, cast

import pytest
from langchain_core.messages import AIMessage
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent_runtime import prepared_run_materializer, prepared_run_participants
from app.agent_runtime.prepared_run_dispatch import handle_for, invoke_prepared
from app.agent_runtime.prepared_run_input_policy import PreparedRunUnsupportedError
from app.agent_runtime.prepared_run_registry import get_prepared_run_registry
from app.config import settings
from app.guardrails.pinning import RUN_METADATA_KEY
from app.guardrails.rules import BUILTIN_TEMPLATES, Rule, RuleSet
from app.guardrails.templates import rules_to_json
from app.models.conversation_run import ConversationRun
from app.models.organization import Tenant
from app.services import prepared_run_adapter
from tests.authz import prepared_adapter_testkit as akit
from tests.authz import prepared_materialization_testkit as kit

SECRET = "sk-abcdefghijklmnopqrstuvwx"


def _scripts(seeded: akit.Seeded) -> dict[str, list[AIMessage]]:
    return {seeded.root_model: [AIMessage("네")], seeded.child_model: []}


async def _materialize(
    db: AsyncSession, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, text: str
) -> tuple[Any, akit.Seeded, list[Any], list[Any]]:
    kit.fresh_registry(monkeypatch)
    akit.patch_keys(monkeypatch)
    seeded = await akit.seed(db)
    await db.commit()
    prepare = akit.resolver(seeded, tmp_path, messages_history=[{"role": "user", "content": text}])
    roots: list[Any] = []
    children: list[Any] = []
    compile_root = prepared_run_materializer.compile_root
    compile_children = prepared_run_materializer.compile_children

    def root_spy(**kwargs: Any) -> Any:
        roots.append(kwargs)
        return compile_root(**kwargs)

    def children_spy(*args: Any, **kwargs: Any) -> Any:
        children.append(kwargs)
        return compile_children(*args, **kwargs)

    monkeypatch.setattr(prepared_run_materializer, "compile_root", root_spy)
    monkeypatch.setattr(prepared_run_materializer, "compile_children", children_spy)
    lease, _ = await akit.materialize(seeded, prepare, _scripts(seeded), kit.FakeMcpServer())
    return lease, seeded, roots, children


@pytest.mark.asyncio
async def test_a_guarded_run_stays_on_the_lease_and_blocks_a_secret(
    db: AsyncSession, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "guardrail_default_template", "kr-default")
    materials: list[Any] = []
    digest = prepared_run_participants.compute_prepared_run_digest

    def digest_spy(material: Any) -> str:
        materials.append(material)
        return digest(material)

    monkeypatch.setattr(prepared_run_participants, "compute_prepared_run_digest", digest_spy)

    lease, seeded, roots, children = await _materialize(
        db, tmp_path, monkeypatch, f"이 키로 호출해: {SECRET}"
    )

    # Probe without the guardrail, final pass with it, for the root and the children.
    assert [kwargs.get("guard") is not None for kwargs in roots] == [False, True]
    assert [kwargs.get("guard") is not None for kwargs in children] == [False, True]
    (material,) = materials
    assert "guardrails" in material.versions
    result = await invoke_prepared(handle_for(lease))
    reply = result["messages"][-1]
    assert "가드레일 정책(secrets)" in str(reply.content)
    assert seeded.root_model not in kit.SENDS  # the model was never called
    await get_prepared_run_registry().terminalize(seeded.run_id)


@pytest.mark.asyncio
async def test_without_guardrails_the_lease_compiles_as_before(
    db: AsyncSession, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "guardrail_default_template", "")

    lease, seeded, roots, _ = await _materialize(db, tmp_path, monkeypatch, "hi")

    assert all(kwargs.get("guard") is None and kwargs.get("tool_risk") is None for kwargs in roots)
    result = await invoke_prepared(handle_for(lease))
    assert result["messages"][-1].content == "네"
    await get_prepared_run_registry().terminalize(seeded.run_id)


@pytest.mark.asyncio
async def test_approval_rules_still_fall_back(
    db: AsyncSession, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    rules = RuleSet(
        "company:approval",
        1,
        (Rule("ids", ("kr",), frozenset({"tool_args"}), "approval", frozenset({"RRN"})),),
    )
    monkeypatch.setattr(prepared_run_adapter, "effective_rules", lambda _cfg: rules)

    with pytest.raises(PreparedRunUnsupportedError) as error:
        await _materialize(db, tmp_path, monkeypatch, "hi")

    assert error.value.code.endswith(":guardrail_policy")


async def _set_company(db: AsyncSession, seeded: akit.Seeded, guardrails: dict[str, Any]) -> None:
    tenant = await db.get(Tenant, seeded.graph.context.tenant_id)
    assert tenant is not None
    tenant.settings = {**(tenant.settings or {}), "guardrails": guardrails}


@pytest.mark.asyncio
async def test_the_company_tool_risk_reaches_the_lease(
    db: AsyncSession, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    kit.fresh_registry(monkeypatch)
    akit.patch_keys(monkeypatch)
    seeded = await akit.seed(db)
    await _set_company(db, seeded, {"mode": "off", "tool_risk": {"external_mutation": "block"}})
    await db.commit()
    prepare = akit.resolver(seeded, tmp_path)

    lease, _ = await akit.materialize(seeded, prepare, _scripts(seeded), kit.FakeMcpServer())

    root_tools = set(lease.runnable.nodes["tools"].bound.tools_by_name)
    assert "lookup" not in root_tools  # the MCP tool is an external change
    assert "ask_user" in root_tools
    await get_prepared_run_registry().terminalize(seeded.run_id)


@pytest.mark.asyncio
async def test_a_chat_turn_keeps_the_rules_it_was_pinned_to(
    db: AsyncSession, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    kit.fresh_registry(monkeypatch)
    akit.patch_keys(monkeypatch)
    seeded = await akit.seed(db)
    await _set_company(db, seeded, {"mode": "off"})  # changed after the turn was pinned
    run = await db.get(ConversationRun, seeded.run_id)
    assert run is not None
    run.metadata_json = {
        **(run.metadata_json or {}),
        RUN_METADATA_KEY: {"rules": rules_to_json(BUILTIN_TEMPLATES["kr-default"])},
    }
    await db.commit()
    prepare = akit.resolver(
        seeded, tmp_path, messages_history=[{"role": "user", "content": f"키 {SECRET}"}]
    )

    lease, _ = await akit.materialize(seeded, prepare, _scripts(seeded), kit.FakeMcpServer())
    result = await invoke_prepared(handle_for(lease))

    assert "가드레일 정책(secrets)" in str(result["messages"][-1].content)
    await get_prepared_run_registry().terminalize(seeded.run_id)


@pytest.mark.asyncio
async def test_every_compiled_child_gets_the_guardrail_and_tool_risk(
    db: AsyncSession, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from dataclasses import replace

    from deepagents.middleware.subagents import create_sub_agent

    from app.guardrails.middleware import GuardrailMiddleware
    from app.guardrails.tool_risk import ToolRiskMiddleware

    monkeypatch.setattr(settings, "guardrail_default_template", "kr-default")
    kit.fresh_registry(monkeypatch)
    akit.patch_keys(monkeypatch)
    seeded = await akit.seed(db)
    await _set_company(
        db,
        seeded,
        {
            "mode": "default",
            "default_template": "builtin:kr-default",
            "tool_risk": {"code_execution": "block"},
        },
    )
    await db.commit()
    compiled: list[dict[str, Any]] = []

    def recording_child(spec: dict[str, Any], **kwargs: Any) -> Any:
        compiled.append(spec)
        return create_sub_agent(cast(Any, spec), **kwargs)

    base_seams = kit.seams
    monkeypatch.setattr(
        kit, "seams", lambda *a, **k: replace(base_seams(*a, **k), compile_child=recording_child)
    )

    lease, _ = await akit.materialize(
        seeded, akit.resolver(seeded, tmp_path), _scripts(seeded), kit.FakeMcpServer()
    )

    assert compiled, "children were compiled"
    for spec in compiled:
        kinds = {type(m) for m in spec["middleware"]}
        assert GuardrailMiddleware in kinds and ToolRiskMiddleware in kinds
    await get_prepared_run_registry().terminalize(seeded.run_id)
