# W3-out 삭제 분석 — M1+M2 도입 전 정리 후보

> 작성: bezos · 2026-05-03 · scope: backend SSE 영속화 경로
> Plan: ~/.claude/plans/1-ux-quirky-canyon.md
> 결론 한 줄: **삭제할 만한 dead code는 거의 없다.** 대부분 "M2 통합 시 함께 단순화"가 정답. 진짜 함정은 BrokerRegistry process-local 싱글턴의 테스트 누수.

---

## 1) 삭제 후보

### A. M2 통합과 함께 사라질 코드 (즉시 제거 OK)

| 위치 | 항목 | 근거 |
|---|---|---|
| `app/routers/conversations.py:217-239` | `_persist_trace()` 함수 전체 | M2에서 `trace_storage.finalize_turn(...)` 직호출로 대체. 4개 endpoint(`send/resume/edit/regenerate`)의 `on_complete=lambda: _persist_trace(...)` 전부 같이 교체. |
| `app/routers/conversations.py:433/459/545/634` | 각 endpoint 진입부 `trace_sink: list = []` + `msg_id_sink: list = []` 보일러플레이트 (4회 반복) | M2에서 broker가 publish + persist_callback 둘 다 받으므로 sink 주입 자체가 불필요해진다. `_sse_handler`(또는 `_resolve_agent_context`)가 broker 1개를 만들고 callback 클로저까지 묶어주면 4 endpoint에서 8줄씩 사라진다. **권고: M2 작업 시 같이 정리**. |
| `app/agent_runtime/streaming.py:62-63 docstring` | "trace_sink (optional, W5)" 설명 + `trace_sink/msg_id_sink` keyword | M2 후 caller는 `broker`를 통해 동일한 정보를 얻는다. **단, 즉시 제거는 위험** — `tests/test_streaming.py` 25개 + `tests/test_executor.py`의 stream_agent_response patch가 sink keyword 의존. **권고: M2에서는 backward-compat keyword로 유지, M5/M6 후속 PR에서 sink 제거.** |
| `app/models/message_event.py:46-48` | `completed_at: datetime \| None` 컬럼 | Phase 1은 "스트림 종료 시점에 한 번 set"이라 `created_at`과 의미 중복. M2에서 `status='streaming'`/`'completed'`/`'failed'` + `updated_at`이 들어오면 `completed_at`은 **status='completed' 전이 시각**으로 의미 협소화 가능. **권고: 즉시 삭제 X — m34 마이그레이션은 ADD COLUMN만 하고, `completed_at`은 한 사이클 더 보존**(legacy reader/share router가 안 보지만, JSON dump에 노출 가능). M5 정리에서 drop 검토. |

### B. 굳이 안 건드려도 됨 (보존 권고)

| 위치 | 항목 | 이유 |
|---|---|---|
| `app/services/trace_storage.py:43-86` | `record_turn()` | 계획서 명시: "기존 `record_turn`은 backward-compat shim". `tests/test_shares_router.py:175`, `tests/test_trace_storage.py` 12개가 직접 호출. M2에서는 `finalize_turn`을 호출하는 thin wrapper로 변환해 호환 유지. |
| `app/services/trace_storage.py:28-40` | `_extract_msg_id()` | M2 이후에도 fallback 경로(예: 첫 batched flush 직전 `assistant_msg_id`가 caller 손에 없을 때) 활용 가능. 모듈 internal helper로 유지. |
| `app/services/trace_storage.py:89-108` | `get_traces_for_conversation` / `get_trace_by_msg_id` | 둘 다 보존. 후자는 M3 GET resume의 replay 경로가 그대로 사용. 전자는 share router가 사용. |
| `_sse_handler` (conversations.py:182) | 통째로 | P0-A SIMPLIFY로 막 추출된 헬퍼. M2는 시그니처에 `run_id` + `extra_headers={"X-Run-Id": ...}`만 추가하면 된다. 함수 자체는 유지. |
| `MessageEvent.linked_message_ids` | 컬럼 + W6 hydration 경로 | shared page chip이 사용 중. M2 변경 무관. |

### C. 진짜 dead code (스캔 결과)

- **없음.** 위에서 만진 4개 파일의 함수/메서드는 모두 1+ 호출처가 있다. `grep -rn _persist_trace` `record_turn` `get_traces_for_conversation` `get_trace_by_msg_id` `stream_agent_response` `_sse_handler` 모두 활성 호출처 확인.

---

## 2) M1+M2 영향 받는 기존 테스트

### High risk — M2 작업에서 직접 갱신 필요

| 파일 | 케이스 수 | 위험 / 필요한 변화 |
|---|---|---|
| `tests/test_conversations_router.py` | 19 (그 중 streaming 4: send/resume/edit/regenerate) | **BrokerRegistry 누수**. process-local singleton dict이라 테스트 간 상태가 새는 즉시 flaky. M2 작업 시 `conftest.py`에 `autouse fixture`로 `registry._brokers.clear()` 또는 `evict_expired(ttl_seconds=0)` 호출. send 테스트는 `X-Run-Id` 응답 헤더 검증 신규 추가 권고. |
| `tests/test_trace_storage.py` | 12 | `record_turn` shim 시 `MessageEvent.status='completed'`, `updated_at` 채워지는지 추가 검증. 기존 단언은 그대로 통과해야 함(필드만 추가되면). 회귀 신호로 활용. |

### Medium risk — 시그니처 호환만 챙기면 통과

| 파일 | 케이스 수 | 메모 |
|---|---|---|
| `tests/test_streaming.py` | 25 | `stream_agent_response`에 `broker=None, persist_callback=None` 추가 시 default-None 유지. 모든 기존 호출이 keyword-arg라 안전. `emit()`이 broker None일 때 publish skip하는지 분기 검증 신규 1건. |
| `tests/test_executor.py` | 13 (stream_agent_response patch 13건) | mock 함수 시그니처가 자유로워서 새 keyword 추가는 무해. **단, executor가 broker=None을 명시적으로 전달하는지 확인** — 안 하면 mock에 unexpected kwarg 전달 안 함이라 OK. 기본값으로 None 흐르면 끝. |
| `tests/test_chat_integration.py` | 약 6 | 풀 스트림 e2e. broker registry 누수 위험은 위와 동일. send response 헤더에 `X-Run-Id` 노출되면 OK. |

### Low risk — 사실상 무영향

| 파일 | 메모 |
|---|---|
| `tests/test_shares_router.py` | `record_turn` 호출(seed)만 사용. 시그니처 동일. |
| `tests/test_migration_m{18,20,21,22}.py` | M2에서 신규 `tests/test_migration_m34.py` 추가가 본 PR 분량(round-trip + idempotent). 기존 m18~m33은 변경 없음. |
| 기타 ~600개 | 직접 영향 없음. |

---

## 3) 주의사항 (구현자 = jensen 대상)

1. **BrokerRegistry process-local singleton 테스트 누수가 1순위 함정.** `app/agent_runtime/event_broker.py`에 모듈-레벨 `registry = BrokerRegistry()`를 두는 즉시 pytest 세션 전체에 broker가 누적된다. `backend/tests/conftest.py`에 다음을 **반드시** 추가:
   ```python
   @pytest.fixture(autouse=True)
   def _clear_event_broker_registry():
       from app.agent_runtime import event_broker
       event_broker.registry._brokers.clear()  # 또는 reset 메서드 추가
       yield
       event_broker.registry._brokers.clear()
   ```
   안 하면 `test_chat_integration` + `test_conversations_router`가 random 순서로 실패한다.

2. **m34 마이그레이션 dialect 분기 필수.** SQLite(테스트 in-memory)는 (a) `CREATE INDEX CONCURRENTLY` 미지원, (b) PG ENUM 미지원. 패턴은 `m20_add_health_check_history` / `m21_add_daily_spend_aggregates`의 `_has_table/_has_column/_has_index` + dialect helper 그대로 답습. CHECK 제약(`status IN ('streaming','completed','failed')`)이 PG/SQLite 둘 다에서 가장 단순.

3. **`CREATE INDEX CONCURRENTLY`는 트랜잭션 안에서 실행 불가.** alembic은 기본 트랜잭션을 연다. `with op.get_context().autocommit_block():` 안에서 `op.execute("CREATE INDEX CONCURRENTLY ...")` 호출. SQLite 분기에서는 일반 `op.create_index` 사용.

4. **`_persist_trace` → `finalize_turn` 교체 시 fresh session 패턴 유지.** 현 `_persist_trace`는 `async with async_session() as session` + `await session.commit()`. SSE generator의 finally에서 호출되므로 request-scoped db는 이미 close됨. `finalize_turn`도 같은 가정으로 호출자 측 책임 분리(또는 finalize 내부에서 `async_session()` 열기) — 둘 중 하나로 통일하고 docstring 명기.

5. **`emit()`의 broker.publish는 hot path.** Token chunk마다 50ms 간격으로 호출. `asyncio.Queue.put_nowait`는 동기지만 lock 경합이 있다. 측정 후 회귀 보이면 broker 일괄 push 옵션. 일단은 단순 path로.

6. **`X-Run-Id` 헤더는 SSE generator 시작 전에 결정돼야 한다.** `stream_agent_response` 안에서 msg_id가 만들어지므로(streaming.py:76) 현재 흐름은 라우터가 사전에 알 수 없음. 해결: (a) 라우터가 `run_id = uuid.uuid4()`를 미리 만들고 `stream_agent_response(..., run_id=run_id)`로 주입 — streaming.py:76 라인의 `msg_id = str(uuid.uuid4())`를 caller-injectable로. 또는 (b) `_sse_handler`가 generator 첫 chunk를 peek해서 message_start의 id를 추출 (복잡, 비추). **(a) 권장.**

7. **`record_turn` 시그니처 보존.** shim화하더라도 caller(test_shares_router 시드)가 `events=[...], raw_msg_ids=[...]` 호출 형태 유지. 내부에서 `finalize_turn(... status='completed')` 호출로 위임.

8. **`MessageEvent.completed_at`은 m34에서 손대지 말 것.** 둘 다 NULL 허용 컬럼이고 의미만 살짝 겹친다. 한 사이클 보존 후 정리.

9. **share router 회귀 0 가드.** `tests/test_shares_router.py:148`의 시드 케이스가 회귀 신호로 가장 빠르게 깨진다. M2 PR pre-push 게이트에서 이 스위트만이라도 먼저 통과 확인.

---

## TL;DR

- **삭제 1건만**: `_persist_trace()` (M2에서 `finalize_turn`로 전환과 함께).
- **단순화 1건**: 4개 endpoint의 `trace_sink/msg_id_sink` 보일러플레이트 → broker 주입으로 흡수.
- **나머지는 보존**: backward-compat 유지가 회귀 위험 가장 낮음.
- **진짜 위험은 코드가 아니라 테스트 격리**: `BrokerRegistry` autouse fixture 필수.
