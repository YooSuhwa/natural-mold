# CHECKPOINT — W3-out: GET-based Stream Resume (M1+M2)

> 첫 TTH 사이클 — backend foundation. M3+M4(GET endpoint+lifecycle), M5+M6(frontend+integration)는 다음 세션.
> 참조: `~/.claude/plans/1-ux-quirky-canyon.md`
> 사용자 결정: m34 status enum 추가, 재연결 시 명시적 인디케이터 (M5에서 구현)

---

## M1: EventBroker primitive (Day 1, ~6h)

- [ ] `backend/app/agent_runtime/event_broker.py` 신설
  - `BrokeredEvent` TypedDict (`{id, event, data}`)
  - `EventBroker(run_id, buffer_size=2000)`: `publish()`, `subscribe(after_id) -> AsyncIterator`, `close(error=None)`, `is_closed`, `last_event_id`
  - ring buffer = `collections.deque(maxlen=2000)`
  - listeners = `set[asyncio.Queue]`, queue maxsize=512
  - `BrokerRegistry`: `get_or_create(run_id)`, `get(run_id)`, `evict_expired(ttl=300)`, `close_for_conversation(conv_id)`
  - `registry: BrokerRegistry = BrokerRegistry()` (process-local singleton)
- [ ] `backend/tests/agent_runtime/test_event_broker.py` 신설
  - publish/subscribe 단일 listener
  - 멀티 listener 동시 subscribe (broadcast)
  - ring buffer maxlen 초과 시 oldest drop
  - subscribe(after_id=...)가 after_id 이후만 받음
  - close 후 subscribe = buffer만 받고 즉시 종료
  - queue maxsize 초과 시 slow listener disconnect

- 검증: `cd backend && uv run ruff check app/agent_runtime/event_broker.py && uv run pytest tests/agent_runtime/test_event_broker.py -v`
- done-when: 신규 테스트 모두 통과, ruff clean
- 상태: **done** (jensen S2 — 21 tests PASS, ruff clean)

---

## M2: streaming.py 통합 + batched persistence + m34 마이그레이션 (Day 2, ~7h)

### M2-1: m34 마이그레이션
- [ ] `backend/app/models/message_event.py` — `status: Mapped[str]`(server_default='completed'), `updated_at: Mapped[datetime]`
- [ ] `backend/alembic/versions/m34_message_events_streaming_status.py`
  - `status` 컬럼 추가 (CHECK 제약 또는 ENUM 둘 중 alembic-friendly한 방식)
  - `updated_at TIMESTAMP` 추가
  - `CREATE INDEX CONCURRENTLY idx_message_events_status ON message_events(conversation_id, status)`
- [ ] `cd backend && uv run alembic upgrade head` 검증

### M2-2: trace_storage.py 확장
- [ ] `append_events(db, *, conversation_id, assistant_msg_id, events_chunk, status='streaming')` UPSERT
  - 기존 row 있으면 events 리스트 concat + last_event_id 갱신 + updated_at = now()
  - 없으면 INSERT
  - **dedup by id** (boundary 중복 방지) — application-side or SQL-side
- [ ] `finalize_turn(db, *, assistant_msg_id, status='completed', raw_msg_ids=None)` — `_persist_trace`의 final-write 책임 흡수
- [ ] 기존 `record_turn`은 backward-compat shim 또는 internal helper로 유지

### M2-3: streaming.py 통합
- [ ] `stream_agent_response(...)` 시그니처: `broker: EventBroker | None = None`, `persist_callback: Callable | None = None` 추가
- [ ] `emit()` 내부:
  - `trace_sink.append(...)` → `broker.publish(...)` (broker가 있으면)
  - `flush_buffer.append(...)` 후 임계치 초과 시 `await persist_callback(flush_buffer)` 호출 후 buffer reset
- [ ] 임계치: 32 events 또는 2초 (마지막 flush로부터 경과)
- [ ] 정상 종료 / 예외 / GraphInterrupt 모두 finally에서 `broker.close()`

### M2-4: conversations.py 통합
- [ ] `_sse_handler`에 `run_id` 파라미터 + 응답 헤더 `X-Run-Id` 노출
- [ ] 4개 POST 핸들러(send/resume/edit/regenerate)가 broker `registry.get_or_create(run_id)` 후 stream에 주입
- [ ] `_persist_trace`를 `finalize_turn` 호출로 단순화

- 검증: `cd backend && uv run ruff check . && uv run pytest tests/ && uv run pyright`
- done-when: ruff clean, 기존 pytest 회귀 0, pyright 0/0, m34 마이그레이션 적용
- 상태: **done** (jensen S3+S4 — 773 PASS, ruff clean, pyright 0/0, alembic 양방향 PASS, BrokerRegistry 누수 차단)

---

## 통합 검증 (M1+M2 완료 후)

- [ ] `cd backend && uv run alembic upgrade head` (m34 적용)
- [ ] `cd backend && uv run ruff check . && uv run pytest tests/ && uv run pyright`
- [ ] 기존 stream 동작 회귀 없음 — POST `/messages` 응답 형식 변경 없음 (헤더만 추가)
- [ ] broker dual-write 검증: 한 stream 진행 중 broker.last_event_id가 갱신되는지 (수동 또는 통합 테스트)
- [ ] `git push`로 pre-push 게이트 통과
- [ ] PR 생성 — backend foundation, 클라이언트 무영향

---

## 다음 세션에서 처리

- M3: GET `/api/conversations/{id}/stream?run_id=&last_event_id=` endpoint
- M4: APScheduler lifecycle + close_for_conversation
- M5: Frontend lastEventId + reconnect indicator
- M6: integration tests + e2e 끊김 시뮬레이션
