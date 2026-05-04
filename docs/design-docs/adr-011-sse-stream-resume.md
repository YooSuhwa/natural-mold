# ADR-011: SSE Stream Resume (W3-out)

## 상태: M1-M5 머지 완료, M6 통합 테스트 + 문서화 진행 중

관련 문서:
- 실행 계획: `~/.claude/plans/1-ux-quirky-canyon.md`
- 마일스톤 진행: `HANDOFF.md` (루트)
- 머지 PR: #116 (M1+M2), #117 (M3+M4), #118 (M5)

---

## 맥락

현재 Moldy 채팅 SSE 스트리밍은 **POST 단방향**으로만 동작했다. 한 turn(`message_start` ~ `message_end`)의 모든 SSE event는 in-memory `trace_sink: list[dict]`에 누적되었다가 turn 종료 후 `_persist_trace`가 batch로 `message_events` 테이블에 적재한다(W5).

문제:
1. **클라이언트 끊김 = 토큰 손실**: 모바일 화면 끄기 / Wi-Fi 토글 / 탭 전환 / 새로고침 시 진행 중인 LangGraph turn이 백엔드에서는 계속 진행되지만, 클라이언트는 그 시점 이후 토큰을 영원히 받지 못한다 (silent fail — 콘솔 에러만).
2. **자동 재연결 의도적 비활성화**: POST는 idempotent하지 않아 재실행 시 새 LangGraph run = 새 LLM 호출. 비용 + 응답 중복.
3. **partial 영속화 부재**: turn 도중 끊기면 그때까지 emit된 event조차 DB에 안 남는다.
4. **W5/W6 인프라 미활용**: trace storage는 이미 모든 event를 보존할 수 있는 구조인데, replay에는 활용되지 않고 있다.

---

## 결정

### 1. 단일 GET endpoint, 서버 내부 분기

```
GET /api/conversations/{conversation_id}/stream?run_id=<uuid>&last_event_id=<id>
```

서버는 `run_id`로 broker 조회 → 두 모드로 분기:
- **broker live** (in-flight): subscribe → `last_event_id` 이후 buffer event + 새 event 라이브 전달. 헤더 `X-Resume-Mode: live`
- **broker dead**: `message_events` 테이블에서 events 슬라이스 (`> last_event_id`) → emit + 즉시 종료. 헤더 `X-Resume-Mode: replay`

`Last-Event-ID` 헤더는 query 가 비면 fallback (SSE 표준, EventSource 호환).

가드 분기는 모두 단일 `404 RESUME_NOT_FOUND` 로 통일 (`rules/security.md` enumeration oracle 방지) — conv 부재, ownership 실패, DB row 부재, broker live 인데 conv_id 불일치, broker.conversation_id None. 분기 구분은 `_log_resume_reject(reason, ...)` 로 서버 로그에만 노출. (HiTL interrupt pending 만 예외적으로 `409 RESUME_INTERRUPT_PENDING` — client 가 `/messages/resume` 으로 와야 한다는 actionable signal 이라 oracle 가치보다 UX 정확성이 크다.)

### 2. EventBroker primitive (per-run, in-memory)

```python
class EventBroker:
    run_id: str
    conversation_id: str | None
    buffer: deque[BrokeredEvent]  # ring, maxlen=2000
    listeners: set[asyncio.Queue]  # maxsize=512 each
    closed: bool

    def publish_nowait(self, evt) -> None
    async def publish(self, evt) -> None       # async wrapper, forward-compat
    async def subscribe(self, after_id) -> AsyncGenerator
    def close(self, *, error=None) -> None     # idempotent
```

- 멀티 listener 동시 attach (멀티 탭 / 모바일+데스크톱 same conv)
- listener queue maxsize 초과 시 slow listener 강제 disconnect (전체 broadcast는 계속). disconnect된 클라이언트는 GET 재시도하면 복구
- `slice_events_after(events, after_id)` — broker subscribe + DB replay 두 경로가 공유하는 슬라이싱 invariant (M3 PR `/simplify` 추출)
- `BrokerRegistry`는 process-local singleton (`dict[run_id, EventBroker]`) — 모듈 레벨 `event_broker.registry`
- in-band 메모리 보호: `max_brokers=256` (LRU eviction, closed 우선), `max_live_age_seconds=1800` (30분 초과 live broker 강제 close)
- TTL 300s `evict_expired` GC + 새 turn 시작 시 같은 conv의 이전 broker 즉시 close (`close_for_conversation`) + APScheduler 60s interval cleanup (M4 wired)

**Multi-worker 한계**: process-local이라 여러 worker 환경에서는 resume이 다른 worker로 라우팅되면 무조건 replay-only가 된다. 현재는 `workers=1` 가정. 후속 트랙에서 Redis pub/sub 또는 sticky routing 도입.

### 3. Run ID = `assistant_msg_id` (UUID)

새 식별자 도입 비용 0. `_prepare_stream_context` 가 `uuid.uuid4()` 한 번 생성 → broker key + `message_start.data.id` + `assistant_msg_id` (DB 컬럼) + POST 응답 헤더 `X-Run-Id` 전부 같은 UUID 로 통일.

`streaming.py` 의 `emit` 클로저는 이 `run_id` 를 받아 `{run_id}-{seq}` 형식으로 SSE `id:` 필드를 발행 — `last_event_id` 비교 / dedup 의 단일 키.

### 4. Mid-stream batched persistence

`stream_agent_response`의 `emit()`이 `flush_buffer`를 누적하고, **32 events 또는 2초** 임계치 도래 시 `persist_callback(flush_buffer)`을 `asyncio.create_task`로 fire-and-forget 호출 → `trace_storage.append_events` UPSERT (`status='streaming'`).

근거:
- per-event flush는 LLM 토큰 chunk 빈도(50ms)에 PG 부하 과다
- 32 events / 2s면 최악 손실 윈도우 = 2초. replay 정확도 충분
- fire-and-forget이라 emit 자체는 latency 0
- 실패 한 chunk 는 `retry_buffer` 로 따로 모았다가 finally 의 final flush 에서 한 번 더 시도 (DB 일시 장애 회복)

`_build_persist_callback` 은 매 호출마다 fresh `async_session()` 으로 own session 을 연다 — SSE generate 의 request-scoped session 과 분리되어 stream 종료 후 callback 잔류로 인한 session leak 방지.

### 5. `message_events` 스키마 확장 (m34)

```sql
ALTER TABLE message_events
  ADD COLUMN status VARCHAR(20) NOT NULL DEFAULT 'completed'
    CHECK (status IN ('streaming', 'completed', 'failed')),
  ADD COLUMN updated_at TIMESTAMPTZ NOT NULL DEFAULT now();

CREATE INDEX idx_message_events_status
  ON message_events(conversation_id, status);
```

- `DEFAULT 'completed' NOT NULL` → PG11+ 메타데이터만 변경 (테이블 스캔 없음)
- 기존 row는 모두 `completed`로 자동 backfill
- replay 시 `status='streaming'`이면 broker가 죽었음을 의미 → 마지막에 `event: stale` SSE 발행 (`reason: broker_lost` + `last_event_id`) 하여 클라이언트가 자동 재시도를 멈추고 사용자 알림

### 6. trace_storage API 확장

```python
async def append_events(db, *, conversation_id, assistant_msg_id, events_chunk, status='streaming') -> MessageEvent | None
async def finalize_turn(db, *, assistant_msg_id, status='completed', raw_msg_ids=None, conversation_id=None) -> MessageEvent | None
```

- `append_events`: UPSERT 패턴. **dedup by id** 필수 (boundary 중복 방지) — 기존 events의 id 집합을 가져와 새 chunk에서 새 id만 필터. 변경 사항 없으면 useless WAL write 회피.
- `finalize_turn`: `_persist_trace`의 final-write 책임 흡수. `completed_at`, `status`, `linked_message_ids` 갱신. row 없으면 `None` 반환 → caller (`_finalize_trace`) 가 `record_turn` fallback.
- 기존 `record_turn`은 backward-compat shim으로 유지 (`IntegrityError` invariant 분리 보존). **M5/M6 후속에서 contract 재평가** 예정.

### 7. POST 호환성 — Dual-write

POST는 기존처럼 generator yield + 부수적으로 `broker.publish_nowait` (dual-write). background-only 강제 X.

근거: background-only로 강제하면 첫 토큰 latency 회귀 위험. dual-write 비용은 microsecond 수준. 끊지 않는 클라이언트는 기존 동작 100% 유지.

### 8. HiTL 호환

기존 `/messages/resume` (interrupt 재개)과 새 `/stream` (네트워크 재연결)은 별도 관심사:
- HiTL: 그래프 실행 흐름 제어 (interrupt → response)
- Stream resume: 네트워크 신뢰성 (연결 끊김 → event replay)

`_is_pending_interrupt(events)` 가 events 마지막이 `interrupt` 이고 `message_end` 가 한 번도 안 왔으면 graph 가 일시정지로 판정 → `409 RESUME_INTERRUPT_PENDING`. Frontend 는 `lastInterruptIdRef` 를 들고 있으면 GET resume skip.

`event_names.py` 단일 source — emit 측과 검증 측이 같은 상수 (`MESSAGE_START`, `INTERRUPT`, `MESSAGE_END`, ...) 를 import. 매직 스트링 rename 시 silent breakage 방지.

### 9. 동시성

- 같은 conversation 동시 2 turn은 금지 (checkpointer가 이미 사실상 lock + `_prepare_stream_context` 가 명시적으로 `close_for_conversation` 호출)
- 같은 run에 동시 listener는 허용 (멀티 탭 broadcast)
- 동시 GET 2개가 race로 같은 event 두 번 보내도 클라이언트의 기존 `createEventDeduper`가 `{msg_id}-{seq}` id 기반으로 dedup
- `subscribe` 의 `yielded_ids` set 으로 buffer snapshot ↔ live tail 경계 dedup (publish_nowait 이 sync 라 미래 await 삽입 시에도 idempotent)

### 10. Lifecycle (M4)

`app/main.py` lifespan:

```python
# startup
register_broker_eviction_job(scheduler, registry, interval_seconds=60, ttl_seconds=300)

# shutdown — 순서가 중요 (rules/async-lifespan.md)
broker_registry.close_all()        # 1. in-flight listener 에 sentinel
await asyncio.sleep(0)             # 2. task switch — subscribe finally 실행 보장
scheduler.shutdown(wait=False)     # 3. APScheduler 종료
await checkpointer.shutdown()      # 4. persistent layer
```

shutdown 순서를 거꾸로 하면 scheduler 가 먼저 죽어서 GC 가 멈추고, listener 는 sentinel 받기 전까지 영원히 `queue.get()` 대기 → SSE generator hang.

### 11. Frontend auto-resume (M5)

`withAutoResume(streamFn, resumeFn, opts)` HOF — POST stream 이 mid-turn 에 끊기면 (`runId` + `lastEventId` 보존) 1s → 2s → 4s → 8s exponential backoff 로 GET resume 재시도. `event: stale` 수신 또는 `409 RESUME_INTERRUPT_PENDING` 또는 max 5회 도달 시 종료.

`onReconnecting/onReconnected/onFailed` 콜백 → `reconnectStateAtom` → `<ReconnectIndicator>` 배지 ("연결 재시도 중...") 입력창 위에 표시.

POST 응답 헤더의 `X-Run-Id` + 매 SSE event 의 `id:` 필드 (parseSSEStream 추출) 를 `runIdRef`/`lastEventIdRef` 에 보관 — abort 시점부터 자연스럽게 GET resume 가능.

---

## 마일스톤 (실제 진행)

| M | 내용 | 기간 | PR | 상태 |
|---|------|------|----|----|
| M1 | EventBroker primitive + 단위 테스트 | ~6h | #116 | ✅ |
| M2 | streaming.py 통합 + batched persistence + m34 마이그레이션 + X-Run-Id | ~7h | #116 | ✅ |
| M3 | GET `/stream` endpoint + 4가지 분기 + replay 슬라이스 + 보안 oracle 통일 | ~6h | #117 | ✅ |
| M4 | APScheduler lifecycle + close_for_conversation + shutdown 순서 정비 | ~3h | #117 | ✅ |
| M5 | Frontend lastEventId + GET resume + 명시적 인디케이터 UI | ~10h | #118 | ✅ |
| M6 | E2E POST→GET 통합 테스트 + ADR-011 정리 | ~6h | (현 PR) | ⏳ |

마일스톤별 PR 분리 — 각 PR마다 회귀 위험 분산 + pre-push 게이트 통과. M3+M4 머지 시점까지 frontend 통합 전이라 사용자 무영향 — M5 머지 시 노출.

---

## 위험 + 완화

| 위험 | 완화 |
|------|------|
| Multi-worker 환경에서 broker process-local | 단기: workers=1 유지. 중기: Redis pub/sub 또는 sticky routing 후속 트랙 |
| `events` JSON row 비대화 (긴 turn) | maxlen=2000 × 평균 200B ≈ 400KB. 한도 초과 시 buffer drop, DB는 최근 1000만 유지 (best-effort) |
| broker가 client보다 빠름 (backpressure) | `asyncio.Queue(maxsize=512)`. 가득 차면 slow listener 강제 disconnect → GET 재시도 |
| HiTL과 stream resume 혼동 | `409 RESUME_INTERRUPT_PENDING` 에러. Frontend는 `lastInterruptIdRef` 있으면 GET resume skip |
| m34 ALTER 락 (prod 큰 테이블) | `DEFAULT 'completed' NOT NULL` 추가는 PG11+ 메타데이터만. 인덱스는 `CREATE INDEX CONCURRENTLY` |
| UPSERT events JSONB concat 비용 | events 작은 동안 무시 가능. 임계치(500KB) 초과 시 `events_chunks` 별도 테이블 (후속 PR) |
| backend 통째 죽음 → broker 같이 사망 | 알려진 한계. DB replay 만 받고 종료 (M3 stale marker 가 사용자 알림). transient network drop (Wi-Fi 토글) 은 broker 살아있어 진짜 live attach. |
| BrokerRegistry capacity 도달 → live broker 강제 close | 메모리 OOM 방지가 turn 보존보다 우선. 멀티 테넌트 도입 시 per-user/conversation sub-cap 추가 (의도적 follow-up) |

---

## 검증

### 자동화
- `cd backend && uv run alembic upgrade head` (m34 적용)
- `cd backend && uv run ruff check .`
- `cd backend && uv run pytest tests/` — broker unit 21 + trace_storage partial + integration test_stream_resume 21 + 회귀 0
- `cd backend && uv run pyright app/` — 0 errors / 0 warnings
- `cd frontend && pnpm lint && pnpm test --run && pnpm build` — withAutoResume / parse-sse / reconnect-indicator 단위 검증 + 회귀 262 tests

### 수동 e2e (M5 머지 시점 통과)
- 개발 서버에서 채팅 시작 → backend SIGKILL → 재시작 → frontend 가 자동 GET resume → partial 토큰 보존 + reconnect 인디케이터 잠깐 노출 후 사라짐
- Wi-Fi 토글 → 인디케이터 잠깐 노출 후 토큰 자연스럽게 이어짐 (X-Resume-Mode: live)

### M6 E2E (현 PR)
`backend/tests/integration/test_stream_resume.py` 의 `W3-out M6` 섹션 — 라우터-단위 합성 테스트와 별개로 진짜 POST 핸들러를 통과시키며:
- A. live attach: POST 가 mid-stream pause → GET 이 들어오면 broker live, buffer replay + 라이브 tail 까지 받음
- B. replay-only: POST 정상 종료 → `evict_expired(ttl_seconds=0)` 강제 → GET 이 DB replay 모드로 떨어짐
- C. stale streaming: `_finalize_trace` no-op patch (crash 시뮬레이션) → row.status='streaming' 잔존 → GET 이 마지막에 `event: stale` 발행
- D. interrupt pending: POST 가 `interrupt` event 만 emit 후 종료 → broker evict → GET 이 `409 RESUME_INTERRUPT_PENDING`

---

## 의도적 follow-up (M6 외)

- 🟠 cross-tenant LRU sub-cap (인증 도입 PR 과 함께)
- 🟡 multi-worker — Redis pub/sub 또는 sticky routing
- 🟡 `get_conversation` + `get_agent_for_user` schema-level join
- 🟡 `evict_expired` dirty flag (워커=1 가정 하에 미적용)
- 🟡 turn 당 events 5000+ 시 `events_chunks` 별도 테이블

---

## 결정 근거 요약 (TL;DR)

W5 trace storage 인프라가 이미 모든 SSE event를 영속화 가능한 구조로 설계됐다 (`{msg_id}-{seq}` event_id, 순서 보장, dedup 가능). 여기에 **in-memory broker 한 겹**을 추가하면 (a) 클라이언트가 끊겨도 토큰을 잃지 않고, (b) 비용 중복(LLM 재호출) 없이 이어볼 수 있고, (c) 멀티 디바이스 동기 표시까지 자연스럽게 따라온다. 비용은 ~6일 개발 + lightweight m34 마이그레이션 + per-run ~400KB 메모리. ROI 양수. 운영상 알려진 한계 (process-local broker, backend 통째 죽음 시 DB replay 로 fallback) 는 명시적으로 받아들였다.
