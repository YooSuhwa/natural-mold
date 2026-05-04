"""Unit tests for ``app.agent_runtime.event_broker`` (W3-out M1).

CHECKPOINT.md M1 done-when:
- publish/subscribe (single + multi listener)
- ring buffer maxlen 초과 시 oldest drop
- subscribe(after_id=...) → after_id 이후만
- close 후 subscribe = buffer만 받고 즉시 종료
- queue maxsize 초과 시 slow listener disconnect (다른 listener 정상)
- registry idempotency / evict_expired / close_for_conversation
"""

from __future__ import annotations

import asyncio
import contextlib

import pytest

from app.agent_runtime.event_broker import (
    BrokeredEvent,
    BrokerRegistry,
    EventBroker,
)


def _make_event(seq: int, *, msg_id: str = "run-1") -> BrokeredEvent:
    return {
        "id": f"{msg_id}-{seq}",
        "event": "content_delta",
        "data": {"delta": f"chunk{seq}"},
    }


async def _collect(broker: EventBroker, *, after_id: str | None = None,
                   limit: int | None = None) -> list[BrokeredEvent]:
    events: list[BrokeredEvent] = []
    async for evt in broker.subscribe(after_id=after_id):
        events.append(evt)
        if limit is not None and len(events) >= limit:
            break
    return events


# --------------------------------------------------------------------------
# Core publish/subscribe
# --------------------------------------------------------------------------


async def test_publish_subscribe_single_listener() -> None:
    broker = EventBroker("run-1")
    consumer_task = asyncio.create_task(_collect(broker, limit=3))
    # Yield once so subscribe can register before publish.
    await asyncio.sleep(0)

    for i in range(1, 4):
        await broker.publish(_make_event(i))

    received = await asyncio.wait_for(consumer_task, timeout=1.0)
    assert [e["id"] for e in received] == ["run-1-1", "run-1-2", "run-1-3"]
    assert broker.last_event_id == "run-1-3"


async def test_subscribe_after_id_replays_only_newer() -> None:
    """Plan 시나리오 — 5개 publish → subscribe(after_id=event3) → 4,5만."""
    broker = EventBroker("run-1")
    for i in range(1, 6):
        await broker.publish(_make_event(i))

    received = await _collect(broker, after_id="run-1-3", limit=2)
    assert [e["id"] for e in received] == ["run-1-4", "run-1-5"]


async def test_subscribe_no_after_id_replays_full_buffer() -> None:
    broker = EventBroker("run-1")
    for i in range(1, 4):
        await broker.publish(_make_event(i))
    broker.close()  # close so subscribe drains and exits

    received = await _collect(broker)
    assert [e["id"] for e in received] == ["run-1-1", "run-1-2", "run-1-3"]


async def test_subscribe_after_id_unknown_yields_nothing_in_replay() -> None:
    """after_id가 buffer에 없으면 (이미 evict 또는 미래 id) replay 단계에서
    아무것도 안 내보낸다. 라이브 모드에서 새 publish만 받는다."""
    broker = EventBroker("run-1")
    for i in range(1, 4):
        await broker.publish(_make_event(i))

    consumer_task = asyncio.create_task(
        _collect(broker, after_id="nonexistent-id", limit=1)
    )
    await asyncio.sleep(0)
    await broker.publish(_make_event(99))

    received = await asyncio.wait_for(consumer_task, timeout=1.0)
    assert [e["id"] for e in received] == ["run-1-99"]


async def test_multiple_listeners_broadcast() -> None:
    broker = EventBroker("run-1")
    consumer_a = asyncio.create_task(_collect(broker, limit=2))
    consumer_b = asyncio.create_task(_collect(broker, limit=2))
    await asyncio.sleep(0)

    await broker.publish(_make_event(1))
    await broker.publish(_make_event(2))

    received_a = await asyncio.wait_for(consumer_a, timeout=1.0)
    received_b = await asyncio.wait_for(consumer_b, timeout=1.0)
    assert [e["id"] for e in received_a] == ["run-1-1", "run-1-2"]
    assert [e["id"] for e in received_b] == ["run-1-1", "run-1-2"]


# --------------------------------------------------------------------------
# Ring buffer behavior
# --------------------------------------------------------------------------


async def test_ring_buffer_drops_oldest() -> None:
    broker = EventBroker("run-1", buffer_size=3)
    for i in range(1, 6):
        await broker.publish(_make_event(i))
    # Internal buffer should retain only last 3 events.
    assert len(broker._buffer) == 3  # noqa: SLF001
    assert [e["id"] for e in broker._buffer] == [  # noqa: SLF001
        "run-1-3",
        "run-1-4",
        "run-1-5",
    ]
    assert broker.last_event_id == "run-1-5"


async def test_subscribe_after_ring_eviction_returns_buffer_only() -> None:
    """버퍼가 oldest를 drop한 후 새 listener는 남아있는 것만 본다."""
    broker = EventBroker("run-1", buffer_size=3)
    for i in range(1, 6):
        await broker.publish(_make_event(i))
    broker.close()

    received = await _collect(broker)
    assert [e["id"] for e in received] == ["run-1-3", "run-1-4", "run-1-5"]


# --------------------------------------------------------------------------
# Close semantics
# --------------------------------------------------------------------------


async def test_close_terminates_subscribe() -> None:
    broker = EventBroker("run-1")

    async def consume() -> list[BrokeredEvent]:
        out: list[BrokeredEvent] = []
        async for evt in broker.subscribe():
            out.append(evt)
        return out

    task = asyncio.create_task(consume())
    await asyncio.sleep(0)

    await broker.publish(_make_event(1))
    # close should make the iterator exit cleanly.
    broker.close()

    received = await asyncio.wait_for(task, timeout=1.0)
    assert [e["id"] for e in received] == ["run-1-1"]
    assert broker.is_closed is True
    assert broker.closed_at is not None


async def test_subscribe_after_close_drains_buffer() -> None:
    broker = EventBroker("run-1")
    for i in range(1, 4):
        await broker.publish(_make_event(i))
    broker.close()

    # Already-closed broker → subscribe drains buffer and exits immediately
    # (no waiting on queue).
    received = await asyncio.wait_for(_collect(broker), timeout=1.0)
    assert [e["id"] for e in received] == ["run-1-1", "run-1-2", "run-1-3"]


async def test_publish_after_close_is_noop() -> None:
    broker = EventBroker("run-1")
    await broker.publish(_make_event(1))
    broker.close()
    await broker.publish(_make_event(2))  # should be silently dropped

    received = await asyncio.wait_for(_collect(broker), timeout=1.0)
    assert [e["id"] for e in received] == ["run-1-1"]
    assert broker.last_event_id == "run-1-1"


async def test_close_is_idempotent() -> None:
    broker = EventBroker("run-1")
    broker.close()
    first_closed_at = broker.closed_at
    broker.close()  # second call should not change closed_at
    assert broker.closed_at == first_closed_at


# --------------------------------------------------------------------------
# Slow-listener backpressure
# --------------------------------------------------------------------------


async def test_slow_listener_disconnect() -> None:
    """가득 찬 queue를 가진 slow listener는 disconnect되고 fast listener는 정상."""
    broker = EventBroker("run-1", listener_queue_maxsize=3)

    fast_received: list[BrokeredEvent] = []
    slow_received: list[BrokeredEvent] = []
    started = asyncio.Event()

    async def fast_consume() -> None:
        async for evt in broker.subscribe():
            fast_received.append(evt)

    async def slow_consume() -> None:
        # 등록만 하고 첫 await 후 절대 진행하지 않게 한다.
        agen = broker.subscribe()
        started.set()
        # Pull a single event then sleep — queue will fill.
        try:
            first = await agen.__anext__()
            slow_received.append(first)
        except StopAsyncIteration:
            return
        # Block forever to simulate slow consumer.
        try:
            await asyncio.sleep(10)
        except asyncio.CancelledError:
            pass
        finally:
            await agen.aclose()

    fast_task = asyncio.create_task(fast_consume())
    slow_task = asyncio.create_task(slow_consume())
    await started.wait()
    await asyncio.sleep(0)  # let both register listener queues

    # Publish enough to overflow the slow listener's queue (maxsize=3).
    # Yield between publishes so the fast listener drains its own queue
    # (maxsize is per-broker, so both listeners share the bound).
    for i in range(1, 11):
        await broker.publish(_make_event(i))
        await asyncio.sleep(0)

    # Slow listener should have been kicked out → only 1 broker listener now.
    # Allow event loop to drain.
    await asyncio.sleep(0.05)

    # Confirm slow listener was removed.
    assert len(broker._listeners) == 1  # noqa: SLF001

    # Fast listener should still receive all 10 events; close to terminate.
    broker.close()
    await asyncio.wait_for(fast_task, timeout=1.0)
    assert [e["id"] for e in fast_received] == [f"run-1-{i}" for i in range(1, 11)]

    # Tear down the slow consumer (it's still parked on sleep).
    slow_task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await slow_task


# --------------------------------------------------------------------------
# BrokerRegistry
# --------------------------------------------------------------------------


def test_registry_get_or_create_idempotent() -> None:
    reg = BrokerRegistry()
    b1 = reg.get_or_create("run-x", conversation_id="conv-1")
    b2 = reg.get_or_create("run-x")
    assert b1 is b2
    assert b1.conversation_id == "conv-1"


def test_registry_get_or_create_replaces_closed() -> None:
    """Closed broker는 같은 run_id로 재생성 시 새 인스턴스로 교체."""
    reg = BrokerRegistry()
    b1 = reg.get_or_create("run-x")
    b1.close()
    b2 = reg.get_or_create("run-x")
    assert b2 is not b1
    assert not b2.is_closed


def test_registry_get_returns_none_for_unknown() -> None:
    reg = BrokerRegistry()
    assert reg.get("does-not-exist") is None


def test_registry_evict_expired_removes_closed() -> None:
    reg = BrokerRegistry()
    b = reg.get_or_create("run-x")
    b.close()
    # ttl=0 → 모든 닫힌 broker는 즉시 evict 대상.
    evicted = reg.evict_expired(ttl_seconds=0)
    assert evicted == 1
    assert reg.get("run-x") is None


def test_registry_evict_expired_skips_live() -> None:
    reg = BrokerRegistry()
    reg.get_or_create("run-live")
    evicted = reg.evict_expired(ttl_seconds=0)
    assert evicted == 0
    assert reg.get("run-live") is not None


def test_registry_evict_expired_respects_ttl() -> None:
    reg = BrokerRegistry()
    b = reg.get_or_create("run-x")
    b.close()
    # ttl=300s → 방금 닫힌 broker는 아직 evict 대상 아님.
    evicted = reg.evict_expired(ttl_seconds=300)
    assert evicted == 0
    assert reg.get("run-x") is not None


def test_registry_close_for_conversation() -> None:
    reg = BrokerRegistry()
    b1 = reg.get_or_create("run-1", conversation_id="conv-A")
    b2 = reg.get_or_create("run-2", conversation_id="conv-A")
    b3 = reg.get_or_create("run-3", conversation_id="conv-B")

    closed = reg.close_for_conversation("conv-A")
    assert closed == 2
    assert b1.is_closed
    assert b2.is_closed
    assert not b3.is_closed


def test_registry_close_for_conversation_skips_already_closed() -> None:
    reg = BrokerRegistry()
    b1 = reg.get_or_create("run-1", conversation_id="conv-A")
    b1.close()
    closed = reg.close_for_conversation("conv-A")
    assert closed == 0  # already closed → not double-counted


# --------------------------------------------------------------------------
# Smoke: ensure module-level singleton exists and is the right type.
# --------------------------------------------------------------------------


def test_module_level_registry_singleton() -> None:
    from app.agent_runtime import event_broker as eb

    assert isinstance(eb.registry, BrokerRegistry)


# ---------------------------------------------------------------------------
# In-band memory caps (M2 보강 — M4 APScheduler GC 도래 전 안전망)
# ---------------------------------------------------------------------------


def test_registry_lru_cap_evicts_oldest_closed() -> None:
    """``max_brokers`` 도달 시 가장 오래된 closed broker가 먼저 evict."""
    reg = BrokerRegistry(max_brokers=3)
    b1 = reg.get_or_create("r1")
    b2 = reg.get_or_create("r2")
    b3 = reg.get_or_create("r3")
    b1.close()
    b2.close()  # b1, b2 closed; b3 live
    # 4번째 broker 등록 — b1(가장 먼저 들어온 closed) 이 빠져야 함
    reg.get_or_create("r4")
    assert reg.get("r1") is None  # evicted
    assert reg.get("r2") is b2  # closed but still under cap
    assert reg.get("r3") is b3
    assert reg.get("r4") is not None


def test_registry_lru_cap_force_closes_live_when_all_live() -> None:
    """모든 broker가 live여도 cap 도달 시 가장 오래된 live를 강제 close + pop."""
    reg = BrokerRegistry(max_brokers=2)
    b1 = reg.get_or_create("r1")
    reg.get_or_create("r2")
    # b1, b2 모두 live. 새 broker 추가 시 b1이 강제 close + pop.
    reg.get_or_create("r3")
    assert reg.get("r1") is None
    assert b1.is_closed  # 강제 close됨
    assert reg.get("r2") is not None
    assert reg.get("r3") is not None


def test_registry_evict_expired_force_closes_stale_live() -> None:
    """``max_live_age_seconds`` 초과한 live broker는 강제 close되고 다음 호출에서 evict."""
    from datetime import timedelta

    reg = BrokerRegistry(max_live_age_seconds=10)
    broker = reg.get_or_create("stale-live")
    # created_at을 인위적으로 과거로 조작
    broker.created_at = broker.created_at - timedelta(seconds=20)
    # 1차 호출: 강제 close (return 0 — 아직 closed_at + ttl 미경과)
    evicted = reg.evict_expired(ttl_seconds=300)
    assert evicted == 0
    assert broker.is_closed
    # 2차 호출: ttl=0이면 즉시 pop
    evicted = reg.evict_expired(ttl_seconds=0)
    assert evicted == 1
    assert reg.get("stale-live") is None


def test_registry_evict_expired_skips_recent_live() -> None:
    """방금 생성된 live broker는 강제 close 대상 아님."""
    reg = BrokerRegistry(max_live_age_seconds=1800)
    broker = reg.get_or_create("recent-live")
    reg.evict_expired()
    assert not broker.is_closed
    assert reg.get("recent-live") is broker


@pytest.mark.asyncio
async def test_subscribe_after_close_during_register_emits_sentinel() -> None:
    """close가 subscribe register 직전에 일어나도 subscriber가 sentinel을 받고 종료.

    B1 race 보강 회귀 가드 — ``listeners.add`` 분기에서 ``self._closed`` 가
    True일 때 self-sentinel을 큐에 넣어 subscriber가 무한 await에 갇히지
    않도록 한 변경의 회귀 테스트.
    """
    broker = EventBroker("race-test")
    broker.close()  # already closed before subscribe
    received: list[BrokeredEvent] = []
    async for evt in broker.subscribe():
        received.append(evt)
    # close 후 subscribe → buffer 비었으니 0개 받고 즉시 종료. 무한 대기 X.
    assert received == []


