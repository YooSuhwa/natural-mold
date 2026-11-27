from __future__ import annotations

import json
import re
import uuid
from contextlib import asynccontextmanager
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from deepagents.middleware.filesystem import FilesystemPermission
from langchain_core.tools import tool

from app.agent_runtime import temporal
from app.agent_runtime.run_error_codes import run_error_code
from app.agent_runtime.runtime.prompts import _system_prompt_with_temporal_context
from app.agent_runtime.runtime_config import AgentConfig
from app.agent_runtime.tool_factory import create_builtin_tool
from app.authz.org_context import OrgContext
from app.chat_recovery import identity
from app.chat_recovery.identity import (
    PREPARATION_DIGEST,
    RecoveryPolicyChanged,
    recovery_runtime_digest,
    verify_prepared_recovery_inputs,
)
from app.config import settings
from app.guardrails.tool_risk import ToolRiskPolicy
from app.services.conversation_run_fence import RunFence, run_fence_scope
from app.services.conversation_run_steers import ConversationSteerStore


def _config() -> AgentConfig:
    context = OrgContext(tenant_id=uuid.uuid4(), org_id=uuid.uuid4(), user_id=uuid.uuid4())
    return AgentConfig(
        provider="openai",
        model_name="example",
        model_id=str(uuid.uuid4()),
        api_key="test-only",
        base_url=None,
        system_prompt="test",
        tools_config=[
            {
                "tool_id": str(uuid.uuid4()),
                "definition_key": "test_read",
                "parameters": {"count": 1},
                "credentials": {"token": "test-only"},
            }
        ],
        org_context=context,
        thread_id=str(uuid.uuid4()),
        agent_id=str(uuid.uuid4()),
        user_id=str(context.user_id),
        resolved_model_credential_id=str(uuid.uuid4()),
        guardrail_resolved=True,
    )


def test_recovery_pin_survives_steer_store_rebinding(monkeypatch: pytest.MonkeyPatch) -> None:
    cfg = _config()
    monkeypatch.setattr(settings, "chat_steer_enabled", False)
    legacy = recovery_runtime_digest(cfg)
    assert legacy is not None
    monkeypatch.setattr(settings, "chat_steer_enabled", True)
    original = replace(cfg, steer_store=ConversationSteerStore())
    replacement = replace(original, steer_store=ConversationSteerStore())
    assert recovery_runtime_digest(original) == recovery_runtime_digest(replacement) == legacy
    assert recovery_runtime_digest(cfg) != legacy


def test_config_pin_tracks_effective_authority_and_ignores_the_resume_cursor() -> None:
    cfg = _config()
    original = recovery_runtime_digest(cfg)
    assert original and re.fullmatch(r"[0-9a-f]{64}", original)
    assert recovery_runtime_digest(replace(cfg, checkpoint_id="new-cursor")) == original
    for changed in (
        replace(cfg, org_context=replace(cfg.org_context, tenant_id=uuid.uuid4())),
        replace(cfg, tools_config=[{**cfg.tools_config[0], "parameters": {"count": 2}}]),
        replace(cfg, tools_config=[{**cfg.tools_config[0], "credentials": {"token": "rotated"}}]),
        replace(cfg, api_key="rotated"),
        replace(cfg, resolved_model_credential_id=str(uuid.uuid4())),
        replace(cfg, tool_grants=frozenset({"test_read"})),
        replace(cfg, tool_risk=ToolRiskPolicy({"low": "approval"})),
        replace(cfg, system_prompt="different instructions"),
    ):
        assert recovery_runtime_digest(changed) not in (original, None)


async def test_takeover_crossing_seoul_midnight_keeps_the_prompt_but_the_clock_tool_is_live(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cfg = _config()
    # 23:59 on Nov 27 in Seoul; the SQL column stores a timezone-naive UTC value.
    start = datetime(2026, 11, 27, 14, 59)
    wall = start.replace(tzinfo=UTC)
    coerce = temporal._coerce_now

    def clock(now: datetime | None, *, timezone: str) -> datetime:
        return coerce(now or wall, timezone=timezone)

    monkeypatch.setattr(temporal, "_coerce_now", clock)
    fence = RunFence(
        uuid.uuid4(),
        "worker-a",
        1,
        uuid.uuid4(),
        cfg.org_context,
        uuid.UUID(cfg.thread_id),
        start,
    )
    with run_fence_scope(fence):
        original = _system_prompt_with_temporal_context(cfg.system_prompt)
    wall = datetime(2026, 11, 27, 15, 3, tzinfo=UTC)
    with run_fence_scope(replace(fence, generation=2, worker_instance_id="worker-b")):
        assert _system_prompt_with_temporal_context(cfg.system_prompt) == original
        assert "현재: 2026-11-27 금요일 23시대" in original
        clock_tool = create_builtin_tool("builtin:current_datetime")
        assert clock_tool is not None
        live = json.loads(await clock_tool.ainvoke({}))
        assert (live["date"], live["time"]) == ("2026-11-28", "00:03:00")
    assert "현재: 2026-11-28 토요일 00시대" in _system_prompt_with_temporal_context(
        cfg.system_prompt
    )


def test_unpinned_resources_are_not_reconstructed() -> None:
    cfg = _config()
    for changed in (
        replace(cfg, agent_skills=[{"version": "1"}]),
        replace(cfg, subagents_config=[{"name": "child"}]),
        replace(cfg, tools_config=[{"definition_key": "mcp"}]),
        replace(cfg, workflow_tools=[object()]),
        replace(cfg, model_params={"unknown": object()}),
        replace(cfg, api_key=None),
        replace(cfg, guardrail_resolved=False),
        replace(cfg, resolved_model_credential_id=None),
    ):
        assert recovery_runtime_digest(changed) is None


@tool
def _lookup(query: str) -> str:
    """Look up an example value."""
    return query


async def test_replacement_compares_actual_prepared_memory_and_tools_before_dispatch(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    cfg = _config()
    fence = RunFence(
        uuid.uuid4(), "worker-a", 1, uuid.uuid4(), cfg.org_context, uuid.UUID(cfg.thread_id)
    )
    run = SimpleNamespace(metadata_json={"keep": 1})
    db = AsyncMock()

    @asynccontextmanager
    async def sessions(_fence: RunFence):
        yield db

    monkeypatch.setattr(identity, "open_run_session", sessions)
    monkeypatch.setattr(identity, "lock_run_fence", AsyncMock(return_value=run))
    kwargs = {
        "system_prompt": "actual prompt",
        "tools": [_lookup],
        "middleware": [],
        "interrupts": None,
        "permissions": [FilesystemPermission(operations=["read"], paths=["/x"], mode="allow")],
        "write_policy": "ask",
        "memory_sources": ["/memo.md"],
        "data_dir": tmp_path,
    }
    memo = tmp_path / "memo.md"
    memo.write_text("Old reference", encoding="utf-8")
    with run_fence_scope(fence):
        await verify_prepared_recovery_inputs(cfg, **kwargs)
    assert run.metadata_json["keep"] == 1
    assert re.fullmatch(r"[0-9a-f]{64}", run.metadata_json[PREPARATION_DIGEST])
    db.commit.assert_awaited_once()
    with run_fence_scope(replace(fence, generation=2, worker_instance_id="worker-b")):
        await verify_prepared_recovery_inputs(cfg, **kwargs)
        for changes in (
            {"system_prompt": "new actual prompt"},
            {"tools": []},
            {"write_policy": "off"},
        ):
            with pytest.raises(RecoveryPolicyChanged):
                await verify_prepared_recovery_inputs(cfg, **{**kwargs, **changes})
        memo.write_text("Changed reference", encoding="utf-8")
        with pytest.raises(RecoveryPolicyChanged) as exc:
            await verify_prepared_recovery_inputs(cfg, **kwargs)
        assert run_error_code(exc.value, "runtime_error") == "recovery_policy_changed"
        run.metadata_json = {}
        with pytest.raises(RecoveryPolicyChanged):
            await verify_prepared_recovery_inputs(cfg, **kwargs)
    db.commit.assert_awaited_once()
