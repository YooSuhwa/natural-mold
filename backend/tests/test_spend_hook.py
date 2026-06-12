"""SpendHook 회귀 테스트 — durable chat run lifecycle P5.2.

cancel 된 run 의 usage 처리 계약:
- cancel 전에 emit 된 usage 는 spend 큐에 적재되어 비용 집계에 반영된다.
- usage 가 보고되기 전에 취소된 run 은 hook 을 crash 시키지 않고 조용히 skip 된다.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest

from app.hooks.base import HookContext, HookKind, HookResult
from app.hooks.builtin.spend_hook import SpendHook
from app.services.spend_writer import SpendEntry, spend_queue


def _ctx(kind: HookKind = "agent_invoke") -> HookContext:
    return HookContext(
        request_id="req-spend-1",
        kind=kind,
        user_id=uuid.uuid4(),
        started_at=datetime.now(UTC),
        agent_id=uuid.uuid4(),
        model_id=uuid.uuid4(),
    )


@pytest.mark.asyncio
async def test_spend_hook_skips_canceled_run_without_usage(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """usage 보고 전에 취소된 run — crash 없이 빈 row 적재도 하지 않는다."""
    added: list[SpendEntry] = []
    monkeypatch.setattr(spend_queue, "add", added.append)

    await SpendHook().async_post_call_hook(_ctx(), HookResult(duration_ms=10))

    assert added == []


@pytest.mark.asyncio
async def test_spend_hook_enqueues_usage_emitted_before_cancel(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """cancel 직전까지 emit 된 usage 는 그대로 비용 집계 큐에 적재된다."""
    added: list[SpendEntry] = []
    monkeypatch.setattr(spend_queue, "add", added.append)

    await SpendHook().async_post_call_hook(
        _ctx(),
        HookResult(duration_ms=10, tokens_in=120, tokens_out=45, cost_usd=0.0021),
    )

    assert len(added) == 1
    entry = added[0]
    assert entry.tokens_in == 120
    assert entry.tokens_out == 45
    assert float(entry.cost_usd) == pytest.approx(0.0021)


@pytest.mark.asyncio
async def test_spend_hook_ignores_non_agent_invoke_kinds(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    added: list[SpendEntry] = []
    monkeypatch.setattr(spend_queue, "add", added.append)

    await SpendHook().async_post_call_hook(
        _ctx(kind="tool_call"),
        HookResult(duration_ms=10, tokens_in=10, tokens_out=10, cost_usd=0.01),
    )

    assert added == []
