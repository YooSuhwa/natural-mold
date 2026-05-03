# ADR-011: SSE Stream Resume (W3-out)

## 상태: 진행 중 (M1+M2 backend foundation 단계)

관련 문서:
- 실행 계획: `~/.claude/plans/1-ux-quirky-canyon.md`
- 마일스톤 진행: `CHECKPOINT.md` (루트)

---

## 맥락

현재 Moldy 채팅 SSE 스트리밍은 **POST 단방향**으로만 동작한다. 한 turn(`message_start` ~ `message_end`)의 모든 SSE event는 in-memory `trace_sink: list[dict]`에 누적되었다가 turn 종료 후 `_persist_trace`가 batch로 `message_events` 테이블에 적재한다(W5).

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

클라이언트 분기 부담 제거. 헤더로 관찰성 확보.

### 2. EventBroker primitive (per-run, in-memory)

```python
class EventBroker:
    run_id: str
    buffer: deque[BrokeredEvent]  # ring, maxlen=2000
    listeners: set[asyncio.Queue]  # maxsize=512 each
    closed: bool

    async def publish(self, evt) -> None
    async def subscribe(self, after_id) -> AsyncIterator
    def close(self, error=None) -> None
```

- 멀티 listener 동시 attach (멀티 탭 / 모바일+데스크톱 same conv)
- listener queue maxsize 초과 시 slow listener 강제 disconnect (전체 broadcast는 계속). disconnect된 클라이언트는 GET 재시도하면 복구
- BrokerRegistry는 process-local singleton (`dict[run_id, EventBroker]`)
- TTL 300s 후 GC + 새 turn 시작 시 같은 conv의 이전 broker 즉시 close + APScheduler 60s interval cleanup (M4에서 wiring)

**Multi-worker 한계**: process-local이라 여러 worker 환경에서는 resume이 다른 worker로 라우팅되면 무조건 replay-only가 된다. 현재는 `workers=1` 가정. 후속 트랙에서 Redis pub/sub 또는 sticky routing 도입.

### 3. Run ID = `assistant_msg_id` (UUID, 기존)

새 식별자 도입 비용 0. 기존 `_extract_msg_id` 로직 재사용. POST 응답 헤더 `X-Run-Id: <uuid>`로 노출.

streaming.py의 `msg_id = str(uuid.uuid4())`을 라우터에서 주입(`run_id` 파라미터)으로 변경하여, broker key와 `message_start.data.id`와 `assistant_msg_id`가 모두 동일 UUID로 통일.

### 4. Mid-stream batched persistence

`stream_agent_response`의 `emit()`이 `flush_buffer`를 누적하고, **32 events 또는 2초** 임계치 도래 시 `persist_callback(flush_buffer)`을 `asyncio.create_task`로 fire-and-forget 호출 → `trace_storage.append_events` UPSERT.

근거:
- per-event flush는 LLM 토큰 chunk 빈도(50ms)에 PG 부하 과다
- 32 events / 2s면 최악 손실 윈도우 = 2초. replay 정확도 충분
- fire-and-forget이라 emit 자체는 latency 0

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
- replay 시 `status='streaming'`이면 broker가 죽었음을 의미 → 마지막에 `event: stale` SSE 발행하여 클라이언트가 retry 또는 사용자 알림

### 6. trace_storage API 확장

```python
async def append_events(db, *, conversation_id, assistant_msg_id, events_chunk, status='streaming') -> None
async def finalize_turn(db, *, assistant_msg_id, status='completed', raw_msg_ids=None) -> MessageEvent | None
```

- `append_events`: UPSERT 패턴. **dedup by id** 필수 (boundary 중복 방지) — 기존 events의 id 집합을 가져와 새 chunk에서 새 id만 필터
- `finalize_turn`: `_persist_trace`의 final-write 책임 흡수. `completed_at`, `status`, `linked_message_ids` 갱신
- 기존 `record_turn`은 backward-compat shim으로 유지

### 7. POST 호환성 — Dual-write

POST는 기존처럼 generator yield + 부수적으로 broker.publish (dual-write). background-only 강제 X.

근거: background-only로 강제하면 첫 토큰 latency 회귀 위험. dual-write 비용은 microsecond 수준. 끊지 않는 클라이언트는 기존 동작 100% 유지.

### 8. HiTL 호환

기존 `/messages/resume` (interrupt 재개)과 새 `/stream` (네트워크 재연결)은 별도 관심사:
- HiTL: 그래프 실행 흐름 제어 (interrupt → response)
- Stream resume: 네트워크 신뢰성 (연결 끊김 → event replay)

interrupt 대기 상태에서 stream resume 시도하면 `409 RESUME_INTERRUPT_PENDING` 에러로 안내. Frontend는 `lastInterruptIdRef` 있으면 GET resume skip.

### 9. 동시성

- 같은 conversation 동시 2 turn은 금지 (checkpointer가 이미 사실상 lock)
- 같은 run에 동시 listener는 허용 (멀티 탭 broadcast)
- 동시 GET 2개가 race로 같은 event 두 번 보내도 클라이언트의 기존 `createEventDeduper`가 `{msg_id}-{seq}` id 기반으로 dedup

---

## 마일스톤 (5-7일)

| M | 내용 | 기간 | 상태 |
|---|------|------|------|
| M1 | EventBroker primitive + 단위 테스트 | ~6h | 진행 중 (이번 PR) |
| M2 | streaming.py 통합 + batched persistence + m34 마이그레이션 + conversations.py 통합 + X-Run-Id | ~7h | 진행 중 (이번 PR) |
| M3 | GET `/stream` endpoint + 4가지 분기 + replay 슬라이스 | ~6h | 다음 세션 |
| M4 | APScheduler lifecycle + close_for_conversation | ~3h | 다음 세션 |
| M5 | Frontend lastEventId + GET resume + 명시적 인디케이터 UI | ~10h | 다음 세션 |
| M6 | Integration tests (httpx) + Vitest + e2e 끊김 시뮬레이션 | ~6h | 다음 세션 |

마일스톤별 PR 분리 권장 — 각 PR마다 회귀 위험 분산 + pre-push 게이트 통과.

---

## 위험 + 완화

| 위험 | 완화 |
|------|------|
| Multi-worker 환경에서 broker process-local | 단기: workers=1 유지. 중기: Redis pub/sub 후속 트랙 |
| `events` JSON row 비대화 (긴 turn) | maxlen=2000 × 평균 200B ≈ 400KB. 한도 초과 시 buffer drop, DB는 최근 1000만 유지 (best-effort) |
| broker가 client보다 빠름 (backpressure) | `asyncio.Queue(maxsize=512)`. 가득 차면 slow listener 강제 disconnect → GET 재시도 |
| HiTL과 stream resume 혼동 | `409 RESUME_INTERRUPT_PENDING` 에러. Frontend는 `lastInterruptIdRef` 있으면 GET resume skip |
| m34 ALTER 락 (prod 큰 테이블) | `DEFAULT 'completed' NOT NULL` 추가는 PG11+ 메타데이터만. 인덱스는 prod에서 `CREATE INDEX CONCURRENTLY`로 변경 권장 |
| UPSERT events JSONB concat 비용 | events 작은 동안 무시 가능. 임계치(500KB) 초과 시 `events_chunks` 별도 테이블 (후속 PR) |

---

## 검증

### M1+M2 (이번 PR)
- `cd backend && uv run alembic upgrade head` (m34 적용)
- `cd backend && uv run ruff check .`
- `cd backend && uv run pytest tests/` — 기존 회귀 0 + 신규 broker/trace_storage 테스트 PASS
- `cd backend && uv run pyright app/` — 0 errors / 0 warnings

### M3 이후 (다음 세션)
- pytest + httpx로 4가지 시나리오 (live attach / replay only / stale / interrupt-conflict)
- frontend Vitest로 `withAutoResume` retry/backoff/abort/dedup boundary
- 수동 e2e: 채팅 중 Wi-Fi off/on 토글 → 인디케이터 잠깐 노출 후 토큰 자연스럽게 이어짐

---

## 결정 근거 요약 (TL;DR)

W5 trace storage 인프라가 이미 모든 SSE event를 영속화 가능한 구조로 설계됐다 (`{msg_id}-{seq}` event_id, 순서 보장, dedup 가능). 여기에 **in-memory broker 한 겹**을 추가하면 (a) 클라이언트가 끊겨도 토큰을 잃지 않고, (b) 비용 중복(LLM 재호출) 없이 이어볼 수 있고, (c) 멀티 디바이스 동기 표시까지 자연스럽게 따라온다. 비용은 5-7일 개발 + lightweight m34 마이그레이션 + per-run ~400KB 메모리. ROI 양수.
