"""Per-run SSE EventBroker primitive (W3-out M1).

연결 신뢰성 레이어의 핵심 요소. POST `/messages` 가 publish하면 broker가 ring
buffer에 보관 + 모든 라이브 listener의 asyncio.Queue로 fan-out한다. 끊긴
클라이언트가 GET `/stream?run_id=&last_event_id=` 로 재연결하면 broker가
살아있을 때 누락된 event를 즉시 replay하고, 이어서 새 토큰을 라이브 구독시킨다.

설계 노트
- process-local 단일 프로세스 가정 (workers=1). 멀티-워커는 후속 트랙
  (Redis pub/sub 또는 sticky routing)이 별도 결정.
- asyncio single-threaded — publish/subscribe 사이의 동기 구간(await가
  없는 구간)은 사실상 atomic이라 listener 등록과 buffer snapshot 사이의
  race를 추가 락 없이 제거한다.
- ring buffer가 가득 차면 oldest event는 silently drop. last_event_id가 이미
  ring 밖으로 밀린 client는 broker 단독으로는 메꿀 수 없으니 router 레이어가
  DB replay (`trace_storage.get_trace_by_msg_id`)로 위임한다.
- listener queue가 가득 찬 slow listener는 publish 경로에서 강제 disconnect
  되며, 다른 listener의 broadcast는 영향받지 않는다.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections import deque
from collections.abc import AsyncGenerator
from datetime import UTC, datetime
from typing import Any, TypedDict

logger = logging.getLogger(__name__)


class BrokeredEvent(TypedDict):
    """Per-event payload published on the broker.

    ``id`` 는 SSE 표준 ``id:`` 필드이며 W3-out resume 시 ``last_event_id`` 의
    기준이 된다. 형식은 ``streaming.py`` 의 emit 클로저가 결정한다
    (현재 ``{msg_id}-{seq}``).
    """

    id: str
    event: str
    data: dict[str, Any]


_DEFAULT_BUFFER_SIZE = 2000
_DEFAULT_LISTENER_QUEUE_MAXSIZE = 512

# Memory-protection caps. APScheduler GC (M4) is the primary defense, but these
# in-band limits prevent runaway accumulation in the M1+M2 release window.
_DEFAULT_MAX_BROKERS = 256
_DEFAULT_MAX_LIVE_AGE_SECONDS = 1800  # 30 min — longer than any reasonable turn


class EventBroker:
    """Per-run SSE event broker.

    Args:
        run_id: assistant message uuid (str). LangGraph turn 식별자.
        buffer_size: ring buffer maxlen — `2000`이 기본. 평균 이벤트 200B 가정
            시 약 400KB 메모리 한도.
        listener_queue_maxsize: 개별 listener queue maxsize. backpressure
            안전장치. 가득 차면 해당 listener는 disconnect.
        conversation_id: 같은 conversation 의 새 turn 시작 시
            ``BrokerRegistry.close_for_conversation`` 으로 일괄 close하기 위한
            메타데이터.
    """

    def __init__(
        self,
        run_id: str,
        *,
        buffer_size: int = _DEFAULT_BUFFER_SIZE,
        listener_queue_maxsize: int = _DEFAULT_LISTENER_QUEUE_MAXSIZE,
        conversation_id: str | None = None,
    ) -> None:
        self.run_id = run_id
        self.conversation_id = conversation_id
        self.buffer_size = buffer_size
        self._listener_queue_maxsize = listener_queue_maxsize
        self._buffer: deque[BrokeredEvent] = deque(maxlen=buffer_size)
        # Sentinel `None` signals close to subscribers blocked on `queue.get()`.
        self._listeners: set[asyncio.Queue[BrokeredEvent | None]] = set()
        self._closed = False
        self._error: Exception | None = None
        self._last_event_id: str | None = None
        self.created_at: datetime = datetime.now(UTC).replace(tzinfo=None)
        self.closed_at: datetime | None = None

    @property
    def is_closed(self) -> bool:
        return self._closed

    @property
    def last_event_id(self) -> str | None:
        return self._last_event_id

    @property
    def error(self) -> Exception | None:
        return self._error

    def publish_nowait(self, evt: BrokeredEvent) -> None:
        """Synchronous publish — buffer.append + fan-out to listener queues.

        ``stream_agent_response`` 의 ``emit()`` 클로저가 sync 함수라서 sync
        진입점이 필요. ``publish`` (async)도 내부적으로 이 메서드를 호출.

        Closed broker는 publish를 silently drop한다 (이미 close된 후의 늦은
        publish 가 stale broadcast를 일으키는 것을 방지).

        Slow listener (queue 가득 참) 는 즉시 listeners에서 제거되고 sentinel
        을 받아 자연스럽게 iterator를 종료한다.
        """
        if self._closed:
            return
        self._buffer.append(evt)
        evt_id = evt.get("id")
        if isinstance(evt_id, str) and evt_id:
            self._last_event_id = evt_id
        # Snapshot to allow safe mutation during iteration (slow listeners
        # are removed mid-broadcast).
        for q in list(self._listeners):
            try:
                q.put_nowait(evt)
            except asyncio.QueueFull:
                self._listeners.discard(q)
                # Slow listener detected — disconnect for backpressure
                # protection. 운영 가시성을 위해 logging (악의적 slow consumer
                # 감지 + 정상 운영 disconnect 빈도 추적). evt id는 마지막
                # publish 시점의 SSE id라 대략적 위치 추정 용도.
                logger.warning(
                    "EventBroker slow listener disconnected run_id=%s "
                    "(queue maxsize=%d). last_event_id=%s",
                    self.run_id,
                    self._listener_queue_maxsize,
                    self._last_event_id,
                )
                # Make room for the sentinel so the subscriber's `queue.get()`
                # eventually wakes up and exits cleanly. Drop one buffered
                # event — the listener already lost causality.
                with contextlib.suppress(asyncio.QueueEmpty):
                    q.get_nowait()
                with contextlib.suppress(asyncio.QueueFull):  # pragma: no cover - defensive
                    q.put_nowait(None)

    async def publish(self, evt: BrokeredEvent) -> None:
        """Async wrapper for ``publish_nowait`` (forward-compat).

        publish 자체는 await하지 않으므로 atomic. caller가 async context에서
        편하게 부르도록 제공.
        """
        self.publish_nowait(evt)

    async def subscribe(
        self, after_id: str | None = None
    ) -> AsyncGenerator[BrokeredEvent, None]:
        """Subscribe to events, optionally replaying buffered events past ``after_id``.

        Behavior:
        - ``after_id is None`` → replay full buffer, then enter live mode.
        - ``after_id`` matches a buffered event → replay events strictly
          after, then live mode.
        - ``after_id`` does NOT match (already evicted from ring buffer or
          newer than last buffered) → yield nothing in replay phase, enter
          live mode. Caller (router) is responsible for detecting this case
          (last_event_id present but no replay events) and emitting a stale
          marker via DB replay.

        atomic 보장: ``listeners.add`` 와 ``buffer snapshot`` 사이에 await가
        없어 publish와 subscribe가 단일 task 내에서 race하지 않는다.
        """
        # Snapshot buffer + register listener under same sync execution.
        queue: asyncio.Queue[BrokeredEvent | None] = asyncio.Queue(
            maxsize=self._listener_queue_maxsize
        )
        if not self._closed:
            self._listeners.add(queue)
        else:
            # Defensive against future await insertions in this block:
            # if close() raced with our subscribe entry, ensure subscriber
            # still receives the sentinel and exits cleanly.
            with contextlib.suppress(asyncio.QueueFull):
                queue.put_nowait(None)
        snapshot: list[BrokeredEvent] = list(self._buffer)
        already_closed = self._closed

        try:
            seen_after = after_id is None
            yielded_ids: set[str] = set()
            for evt in snapshot:
                evt_id = evt.get("id")
                if not seen_after:
                    if evt_id == after_id:
                        seen_after = True
                    # Skip events up to and including after_id.
                    continue
                if isinstance(evt_id, str):
                    yielded_ids.add(evt_id)
                yield evt

            # Closed-at-subscribe case: drain buffer only, no live wait.
            if already_closed:
                return

            while True:
                item = await queue.get()
                if item is None:
                    return
                evt_id = item.get("id")
                # Defensive dedup: if the buffer snapshot included an event
                # that also reached the listener via fan-out, skip it. In
                # practice this shouldn't happen (queue is created after
                # buffer events were published) but guarantees idempotency.
                if isinstance(evt_id, str) and evt_id in yielded_ids:
                    continue
                yield item
        finally:
            self._listeners.discard(queue)

    def close(self, *, error: Exception | None = None) -> None:
        """Mark broker closed and signal all live listeners to terminate.

        Idempotent: subsequent calls are no-ops. After close,
        ``publish`` becomes a no-op and ``subscribe`` only drains the
        buffer.
        """
        if self._closed:
            return
        self._closed = True
        self._error = error
        self.closed_at = datetime.now(UTC).replace(tzinfo=None)
        for q in list(self._listeners):
            try:
                q.put_nowait(None)
            except asyncio.QueueFull:
                # Subscriber will eventually drain queue and re-enter
                # `queue.get()` on a now-empty queue, then block. Best-effort:
                # drop one and retry.
                with contextlib.suppress(asyncio.QueueEmpty):  # pragma: no cover - defensive
                    q.get_nowait()
                with contextlib.suppress(asyncio.QueueFull):  # pragma: no cover - defensive
                    q.put_nowait(None)
        self._listeners.clear()


class BrokerRegistry:
    """Process-local registry of EventBrokers keyed by ``run_id``.

    멀티-워커 환경 지원은 후속 트랙. 단일 워커에서는 dict + asyncio
    single-thread 모델로 충분하다 (lock 불필요).

    메모리 보호: M4의 APScheduler GC가 정식 청소부지만, 그 이전 PR
    릴리즈 창에서도 오래된 broker가 무한 누적되지 않도록 in-band
    safeguard 두 가지를 둔다 — (a) ``max_brokers`` 한도 도달 시
    가장 오래된 closed broker부터 eviction, (b) live broker도
    ``max_live_age_seconds`` 초과 시 ``evict_expired`` 가 강제 close.
    """

    def __init__(
        self,
        *,
        max_brokers: int = _DEFAULT_MAX_BROKERS,
        max_live_age_seconds: int = _DEFAULT_MAX_LIVE_AGE_SECONDS,
    ) -> None:
        self._brokers: dict[str, EventBroker] = {}
        self._max_brokers = max_brokers
        self._max_live_age_seconds = max_live_age_seconds

    def get_or_create(
        self,
        run_id: str,
        *,
        conversation_id: str | None = None,
        buffer_size: int = _DEFAULT_BUFFER_SIZE,
    ) -> EventBroker:
        """Idempotent get-or-create.

        같은 run_id로 두 번 호출 시 같은 EventBroker 인스턴스를 반환한다.
        기존 broker가 이미 close된 경우(같은 run_id 재사용은 비정상)에는
        새 broker로 교체한다.

        한도 도달 시 in-band LRU eviction: ``max_brokers`` 초과면 가장
        오래된 closed broker부터 dict에서 pop. 모두 live면 가장 오래된
        live broker를 강제 close + pop. 운영 OOM 방지가 정상 turn 보존
        보다 우선.
        """
        broker = self._brokers.get(run_id)
        if broker is None or broker.is_closed:
            # 같은 run_id 재사용(closed) 케이스에서 자기 자신을 LRU eviction
            # 후보로 만들지 않도록 먼저 pop. 그 후 capacity 검사 → 새 broker
            # 등록. _enforce_capacity가 자기 자신의 dict slot을 evict하는
            # 우연한 정합성에 의존하지 않게 한다.
            self._brokers.pop(run_id, None)
            self._enforce_capacity()
            broker = EventBroker(
                run_id,
                buffer_size=buffer_size,
                conversation_id=conversation_id,
            )
            self._brokers[run_id] = broker
        return broker

    def _enforce_capacity(self) -> None:
        """Drop oldest closed (or oldest live) brokers until under the cap.

        실제로는 insertion-order 기반 FIFO + closed-우선 정책 (true LRU
        아님 — 같은 broker가 ``get_or_create`` 재호출되어도 dict 순서는
        안 바뀐다). 가장 먼저 들어온 broker부터 검사하여 closed면 즉시
        pop, live면 강제 close 후 pop. ``max_brokers - 1`` 까지 비워야
        새 entry가 들어갈 자리 확보.

        ⚠️ 정상 운영 중 live broker 강제 close는 진행 중인 stream을
        끊는다 (subscriber는 sentinel 받음). 메모리 보호가 turn 보존보다
        우선. 멀티 테넌트 도입 시 per-user/conversation sub-cap을 추가
        해야 한 사용자가 다른 사용자의 stream을 끊는 cross-tenant
        eviction을 방지할 수 있다 (M3+ 후속 트랙).
        """
        if len(self._brokers) < self._max_brokers:
            return
        target = self._max_brokers - 1
        for run_id in list(self._brokers.keys()):
            if len(self._brokers) <= target:
                break
            broker = self._brokers[run_id]
            if not broker.is_closed:
                logger.warning(
                    "BrokerRegistry capacity reached (%d) — force-closing live "
                    "broker run_id=%s to make room",
                    self._max_brokers,
                    run_id,
                )
                broker.close()
            self._brokers.pop(run_id, None)

    def get(self, run_id: str) -> EventBroker | None:
        return self._brokers.get(run_id)

    def evict_expired(self, ttl_seconds: int = 300) -> int:
        """Evict closed brokers past TTL + force-close stale live brokers.

        두 단계로 정리:

        1. ``broker.closed_at + ttl_seconds`` 가 과거면 dict에서 pop.
        2. live broker 중 ``broker.created_at + max_live_age_seconds`` 가
           과거면 강제 close (다음 호출에서 1단계로 정리됨).
           정상 turn은 분 단위로 끝나므로 30분 초과 live broker는
           누락된 close() 콜백 또는 finally 미호출의 신호다.

        Returns:
            Number of brokers evicted (closed broker pops only — force-closed
            live brokers는 다음 호출에서 evict).
        """
        now = datetime.now(UTC).replace(tzinfo=None)
        now_ts = now.timestamp()
        closed_cutoff = now_ts - ttl_seconds
        live_cutoff = now_ts - self._max_live_age_seconds
        to_remove: list[str] = []
        for run_id, broker in self._brokers.items():
            if broker.closed_at is None:
                # Force-close stale live broker. Will be evicted on next call.
                if broker.created_at.timestamp() <= live_cutoff:
                    logger.warning(
                        "Force-closing stale live broker run_id=%s "
                        "(age %ds > max %ds)",
                        run_id,
                        int(now_ts - broker.created_at.timestamp()),
                        self._max_live_age_seconds,
                    )
                    broker.close()
                continue
            if broker.closed_at.timestamp() <= closed_cutoff:
                to_remove.append(run_id)
        for run_id in to_remove:
            self._brokers.pop(run_id, None)
        return len(to_remove)

    def close_for_conversation(self, conversation_id: str) -> int:
        """Force-close all live brokers belonging to a conversation.

        같은 conversation 의 새 turn 진입 시 이전 broker를 즉시 회수한다
        (동시 2 turn은 checkpointer lock으로 이미 금지되지만, 이전 turn의
        broker가 여전히 라이브 listener를 들고 있는 경우를 정리).

        Returns:
            Number of brokers closed.
        """
        count = 0
        for broker in list(self._brokers.values()):
            if broker.conversation_id == conversation_id and not broker.is_closed:
                broker.close()
                count += 1
        return count

    def all_brokers(self) -> list[EventBroker]:
        """Snapshot list of all registered brokers (live + closed)."""
        return list(self._brokers.values())

    def close_all(self) -> int:
        """Force-close every live broker (shutdown hook).

        APScheduler 의 ``evict_expired`` GC 보다 더 강한 동작 — TTL 무시하고
        지금 살아있는 모든 broker 의 listener 에 sentinel 을 보내 그래스풀
        종료. lifespan shutdown 단계에서 호출되어 in-flight stream consumer
        가 hang 되지 않게 한다. Idempotent: 이미 closed 인 broker 는 skip.

        Returns:
            Number of brokers actually closed in this call.
        """
        count = 0
        for broker in list(self._brokers.values()):
            if not broker.is_closed:
                broker.close()
                count += 1
        return count

    def clear(self) -> None:
        """Test helper — drop all brokers without closing.

        Production code should not call this; use ``evict_expired`` /
        ``close_for_conversation`` instead.
        """
        self._brokers.clear()


# Module-level singleton. M2의 streaming.py 통합과 M3의 GET resume endpoint가
# 같은 인스턴스를 참조한다.
registry: BrokerRegistry = BrokerRegistry()
