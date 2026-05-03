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
from collections.abc import AsyncIterator
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
    ) -> AsyncIterator[BrokeredEvent]:
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
    """

    def __init__(self) -> None:
        self._brokers: dict[str, EventBroker] = {}

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
        """
        broker = self._brokers.get(run_id)
        if broker is None or broker.is_closed:
            broker = EventBroker(
                run_id,
                buffer_size=buffer_size,
                conversation_id=conversation_id,
            )
            self._brokers[run_id] = broker
        return broker

    def get(self, run_id: str) -> EventBroker | None:
        return self._brokers.get(run_id)

    def evict_expired(self, ttl_seconds: int = 300) -> int:
        """Evict brokers whose ``closed_at + ttl_seconds`` is in the past.

        살아있는(live) broker는 evict하지 않는다 (스트림 진행 중일 수
        있음). APScheduler 60s interval job에서 호출.

        Returns:
            Number of brokers evicted.
        """
        now_ts = datetime.now(UTC).replace(tzinfo=None).timestamp()
        cutoff_ts = now_ts - ttl_seconds
        to_remove: list[str] = []
        for run_id, broker in self._brokers.items():
            if broker.closed_at is None:
                continue
            if broker.closed_at.timestamp() <= cutoff_ts:
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

    def clear(self) -> None:
        """Test helper — drop all brokers without closing.

        Production code should not call this; use ``evict_expired`` /
        ``close_for_conversation`` instead.
        """
        self._brokers.clear()


# Module-level singleton. M2의 streaming.py 통합과 M3의 GET resume endpoint가
# 같은 인스턴스를 참조한다.
registry: BrokerRegistry = BrokerRegistry()
