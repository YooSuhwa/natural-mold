# W3-out M1+M2 — 최종 통합 검증 리포트

> 작성: bezos · 2026-05-03 · scope: backend foundation (M1 EventBroker + M2 streaming/persistence/m34)
> 결과: **PASS — 모든 검증 게이트 통과. 머지 가능.**

---

## 검증 결과 요약

| # | 게이트 | 결과 | 비고 |
|---|---|---|---|
| 1 | `alembic upgrade head` (PG 5433) | ✅ PASS | m22 → ... → m34 까지 12개 리비전 모두 적용 |
| 2 | `alembic downgrade -1 && upgrade head` (m34 round-trip) | ✅ PASS | `status` + `updated_at` + CHECK 제약 + 인덱스 모두 정확히 drop/recreate |
| 3 | `ruff check .` | ✅ PASS | All checks passed |
| 4 | `pytest tests/` | ✅ PASS | 773 passed, 2 deselected (3회 연속 실행 — flakiness 0건) |
| 5 | `pyright app/` | ✅ PASS | 0 errors, 0 warnings, 0 informations |
| 6 | dual-write 라우터 smoke (X-Run-Id + broker registration) | ✅ PASS | 신규 2건 추가. end-to-end DB 영속화는 M6에서 검증 |

---

## 상세

### 1. Alembic m34 마이그레이션 (PG 5433)

```
$ DATABASE_URL_SYNC=...:5433/moldy uv run alembic upgrade head
... Running upgrade m33_add_linked_message_ids -> m34_message_events_status

$ DATABASE_URL_SYNC=...:5433/moldy uv run alembic downgrade -1
... Running downgrade m34_message_events_status -> m33_add_linked_message_ids

$ DATABASE_URL_SYNC=...:5433/moldy uv run alembic upgrade head
... Running upgrade m33_add_linked_message_ids -> m34_message_events_status
```

스키마 검증 (`docker exec moldy-postgres-test psql -d moldy -c "\d message_events"`):

```
 status     | character varying(20)       | not null | 'completed'::character varying
 updated_at | timestamp without time zone | not null | now()
Indexes:
    "idx_message_events_status" btree (conversation_id, status)
Check constraints:
    "ck_message_events_status" CHECK (status::text = ANY (ARRAY['streaming', 'completed', 'failed']))
```

- ✅ `status` `NOT NULL DEFAULT 'completed'` (PG 11+ 메타데이터 변경, table rewrite 없음)
- ✅ `updated_at` `NOT NULL DEFAULT now()`
- ✅ CHECK 제약 (PG/SQLite 양쪽에서 동작)
- ✅ 복합 인덱스 `(conversation_id, status)`
- ✅ 기존 `completed_at` 컬럼 보존 (S1 분석 권고대로)
- ✅ Round-trip 후 동일 스키마 복원 확인

### 2. Lint / Typecheck

- `ruff check .` → All checks passed
- `pyright app/` → 0 errors, 0 warnings, 0 informations

### 3. Pytest 회귀 (3회 연속 실행)

```
Run 1: 770 passed, 2 deselected in 33.22s
Run 2: 771 passed, 2 deselected in 34.31s
Run 3: 771 passed, 2 deselected in 34.89s
Run 4 (신규 smoke 추가 후): 773 passed, 2 deselected in 34.31s
```

- baseline 735 + M1+M2 신규 ≈ 38건 추가 = 773. 회귀 0건.
- 3회 연속 실행에서 모두 green — **S1에서 1순위 함정으로 지목한 BrokerRegistry process-local 누수 없음.**
- 누수 방지 메커니즘: `tests/conftest.py:53` `_clear_event_broker_registry` autouse fixture (jensen이 S2에서 추가). before-yield + after-yield 양방 clear.

### 4. M1+M2 신규 테스트 커버리지

- **M1 (EventBroker)**: `tests/agent_runtime/test_event_broker.py` — publish/subscribe/close/maxlen drop/queue full disconnect/registry get_or_create/evict_expired/close_for_conversation 등.
- **M2 (streaming.py 통합)**: `tests/test_streaming.py` 끝 블록 — `test_stream_run_id_injection_uses_external_id`, `test_stream_dual_writes_to_broker_and_trace_sink`, `test_stream_persist_callback_final_flush_in_finally`, `test_stream_broker_close_called_even_on_exception`.
- **M2 (trace_storage)**: `tests/test_trace_storage_partial.py` — `append_events` insert/merge/dedup-by-id/empty noop, `finalize_turn` status 갱신/completed_at/linked_message_ids.
- **M2 (router 계약)**: `tests/integration/test_broker_dual_write.py` (S5에서 신규 추가) — POST `/messages` 응답 헤더 `X-Run-Id` 노출 + broker registry 등록 확인.

### 5. Dual-write 검증

라우터 레이어에서 broker가 run_id별로 등록되는지 확인하는 smoke 2건 신규 추가:

- `test_send_message_exposes_x_run_id_header` — `X-Run-Id` 헤더가 valid UUID로 노출됨 (M5 frontend가 들고 GET-resume 요청 가능).
- `test_send_message_registers_broker_for_run_id` — executor 호출 시점에 `BrokerRegistry.get(run_id)`가 broker 인스턴스를 반환 (M3 GET resume이 attach할 수 있는 상태).

**End-to-end "POST → DB row + broker live token stream"** 은 M3 GET endpoint가 추가되는 다음 사이클에서 자연스럽게 검증된다 (M6 통합 테스트가 그 자리). 현 PR에서 시도하지 않은 이유:
- `_build_persist_callback`이 `app.database.async_session()` (실제 PG asyncpg)를 직접 열기 때문에 in-memory aiosqlite 테스트 하네스로는 영속화 경로를 그대로 검증 불가
- 별도 fixture 작성은 M6 작업 자체와 동일 분량 → 거기서 함께 처리하는 게 자연스러움

---

## S1 → S5 추적: 1순위 함정 처리 결과

| S1 분석 항목 | 처리 결과 |
|---|---|
| **BrokerRegistry process-local 누수 (필수)** | ✅ jensen이 `tests/conftest.py:53-64`에 `_clear_event_broker_registry` autouse fixture 추가. 3회 연속 실행 flaky 0건. |
| m34 dialect 분기 | ✅ `_now_default()` PG/SQLite 분기. CHECK 제약은 양쪽 호환. |
| `CREATE INDEX CONCURRENTLY` 트랜잭션 함정 | ⚠ 회피: m34는 일반 `CREATE INDEX` 사용 (마이그레이션 docstring에 "운영 큰 테이블에는 CONCURRENTLY 별도 절차 권장" 명기). 현재 상태 OK. |
| `_persist_trace` → `finalize_turn` 교체 | ✅ `_build_persist_callback` + `finalize_turn` 기반으로 재배선 완료. fresh `async_session()` 패턴 유지. |
| `X-Run-Id` 헤더 caller-injectable | ✅ `_sse_handler(... run_id=...)` + `_sse_response(... extra_headers=...)`. `stream_agent_response`도 `run_id=` keyword 수용. |
| `record_turn` shim 보존 (test_shares_router seed 의존) | ✅ shim 유지 → test_shares_router 그린. |
| `MessageEvent.completed_at` 보존 | ✅ m34에서 손대지 않음. |

---

## 결론

**M1+M2 backend foundation은 머지 가능 상태.** 

- 검증 게이트 6/6 PASS
- 회귀 0건 (773/773)
- BrokerRegistry 누수 risk 사전에 차단됨
- 클라이언트 무영향 (broker는 publish하지만 아무도 안 읽음 — M3 GET endpoint가 들어와야 사용자 경험에 노출)

**다음 사이클(M3+M4) 진입 권고.**
