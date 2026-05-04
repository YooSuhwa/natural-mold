# 작업 인계 — feature/w3-out-m3-get-stream (M3 + M4 + 리뷰 fix)

> 새 세션 진입: 본 파일 + `progress.txt` 마지막 4-5 섹션 + `~/.claude/plans/1-ux-quirky-canyon.md`.
> 설계: `docs/design-docs/adr-011-sse-stream-resume.md`.
> ⚠️ 첫 작업: 브랜치 머지 여부 확인 → main 이면 `/sync` 후 M5.

## 마지막 상태

- 브랜치: **`feature/w3-out-m3-get-stream`** (HEAD `358b2ab`, **PR 미생성**)
- main HEAD: `568cfd4` (PR #116 머지 시점), alembic head **m34** (변경 없음)
- backend **815 pass** / pyright 0 errors / ruff clean / frontend lint·test·build clean
- 사용자 무영향 (GET endpoint + lifecycle 정비만, frontend 미통합)

## 이번 사이클 (8 커밋)

| Commit | 내용 |
|---|---|
| `3cf5086` | **M3** — GET `/api/conversations/{id}/stream?run_id=&last_event_id=` (4 분기) |
| `94f6ca8` | **M4** — `register_broker_eviction_job` (60s) + `BrokerRegistry.close_all` + lifespan |
| `1a5a0f5` | pyright fix — `subscribe` `AsyncIterator → AsyncGenerator` |
| `f2e0b33` | **리뷰 BLOCKER+HIGH** — shutdown 순서 / oracle 통일 (단일 404) / broker.conv_id None fail-closed / 운영 로깅 |
| `3c00682` | **리뷰 MEDIUM 7** — `event_names.py` / corrupt evt skip / stale id fallback / tz fix / lowercase 헤더 / `close_for_conversation` 로깅 / `get_agent_for_user` |
| `65a22e0` | **리뷰 NIT 5** — `_normalize_event_id` / `_clear()` underscore / `resume_gone` 제거 / `aclose` 회귀 |
| `358b2ab` | `/simplify` — `slice_events_after` 공용 helper / `_log_resume_reject` / `_format_brokered` 인라인 |

## 다음 작업

1. **M5 — Frontend lastEventId + withAutoResume + reconnect 인디케이터** (~10h, plan M5). 신규 8 + 수정 8 파일
2. **M6 — 통합 테스트 + 끊김 시뮬레이션** (~6h). e2e 4 시나리오
3. M3+M4 PR 먼저 생성 후 머지 → M5 시작 (PR 사이즈 절반) 가능

## 의도적 follow-up

- 🟠 cross-tenant LRU sub-cap (인증 도입 PR 과 함께)
- 🟡 multi-worker 지원 — Redis pub/sub 또는 sticky routing
- 🟡 `get_conversation` + `get_agent_for_user` schema-level join (현재 2 round-trip)
- 🟡 `evict_expired` dirty flag (워커=1 가정 하에 미적용)
- 🟡 turn 당 events 5000+ 시 `events_chunks` 별도 테이블

## 알려진 이슈

- W3-out 글로벌 룰 신규 (`~/.claude/rules/security.md` enumeration oracle / `~/.claude/rules/async-lifespan.md` shutdown 순서) — **다음 세션부터 효력**
- W6 trace 매핑: m32 이전 row `linked_message_ids = NULL`

## 코드 컨벤션 (이번 사이클 정착분)

- **SSE event 이름**: `agent_runtime/event_names.py` 단일 source. 매직 스트링 금지
- **Resume endpoint 가드**: 모든 실패 단일 `404 RESUME_NOT_FOUND`. `_log_resume_reject(reason, ...)` 로 reason 만 서버 로그 (rules/security.md)
- **Shutdown 순서**: in-flight consumer → `await asyncio.sleep(0)` → scheduler → DB (rules/async-lifespan.md)
- **Naive UTC 비교**: `.timestamp()` 회피 (로컬 tz 해석). `datetime` 직접 비교
- **Events 슬라이싱**: `slice_events_after[E: Mapping](events, after_id)` — broker subscribe + DB replay 공유

## 검증

```bash
cd backend && uv run alembic upgrade head && uv run ruff check . && uv run pytest tests/ && uv run pyright
cd frontend && pnpm lint && pnpm test --run && pnpm build
```

## 핵심 파일 (M3+M4 진입점)

- broker: `backend/app/agent_runtime/event_broker.py` (`slice_events_after`, `close_all`, tz fix)
- routes: `backend/app/routers/conversations.py` (`stream_resume`, `_log_resume_reject`, `_replay_resume_generator`)
- lifecycle: `backend/app/scheduler.py` (`register_broker_eviction_job`) + `backend/app/main.py`
- helpers: `backend/app/services/chat_service.py:get_agent_for_user`, `backend/app/agent_runtime/event_names.py`
- 테스트: `backend/tests/integration/test_stream_resume.py` (15건)
- Plan: `~/.claude/plans/1-ux-quirky-canyon.md` (M5/M6 섹션)

## 새 트랙 시작 체크

1. `gh pr view feature/w3-out-m3-get-stream` 머지 여부 확인 (현재 PR 미생성)
2. PR 생성 권장 — backend 자체 완결, 사용자 무영향
3. M5 시작 시: `feature/w3-out-m5-frontend` 신규 브랜치 (현재 브랜치에서 분기)
4. plan M5 섹션 — `withAutoResume` + `lastEventIdRef` + `reconnect-indicator.tsx`
