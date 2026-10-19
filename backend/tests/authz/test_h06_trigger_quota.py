"""H-06 + F-08: a trigger refused by a usage limit is a skipped BLOCKED run (plan 8.7, 11.6)."""

from typing import Any

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.authz import denials
from app.authz.dispatch_block import TRANSIENT, block_reason
from app.tenancy import limit_capacity
from tests.authz.e01_support import add_agent, seed
from tests.authz.h06_support import fresh_limits, set_agent_limit, spend
from tests.authz.test_f08_trigger_blocked import _fire, _install, _runnable, _state, _trigger

__all__ = ["fresh_limits"]
pytestmark = [pytest.mark.asyncio, pytest.mark.usefixtures("fresh_limits")]


def test_quota_is_a_transient_block_reason() -> None:
    error = denials.quota_exceeded("monthly_spend", "agent")
    assert block_reason(error) == "quota_exceeded" and "quota_exceeded" in TRANSIENT


@pytest.mark.parametrize("mode", ["enforce", "legacy"])
async def test_trigger_over_its_limit_is_skipped_and_never_pauses(
    db: AsyncSession, monkeypatch: pytest.MonkeyPatch, mode: str
) -> None:
    await seed(db)
    agent = await add_agent(db)
    agent_id = agent.id
    trigger = await _trigger(db, agent, pause_after=1)
    await set_agent_limit(db, agent_id, "1")
    await spend(db, "1", agent_id=agent_id)
    _install(monkeypatch, mode, _runnable(agent))
    invoke: Any = await _fire(trigger)
    invoke.assert_not_called()
    [run], fresh, _ = await _state(trigger.id)
    expected_reason = "quota_exceeded" if mode == "enforce" else None
    assert (run.status, run.reason_code) == ("skipped", expected_reason)
    assert (fresh.failure_count, fresh.status) == (0, "active")
    assert run.error_message == "quota_exceeded: quota_exceeded"
    limit_capacity.reset_rate_windows()
