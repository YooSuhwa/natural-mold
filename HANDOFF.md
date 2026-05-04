# 작업 인계 — main + PR #116 (W3-out M1+M2 backend foundation)

> 새 세션은 본 파일 + `progress.txt` 마지막 4-5 섹션 + `~/.claude/plans/1-ux-quirky-canyon.md`.
> 디자인 시스템 `docs/design-docs/ADR-010-ui-tokens-and-dialog-shell.md` / W3-out 설계 `docs/design-docs/adr-011-sse-stream-resume.md`.
> ⚠️ 첫 작업: PR #116 머지 확인 → `/sync` → 본 문서 갱신 후 다음 트랙 진입.

## 마지막 상태

- 브랜치: **`feature/w3-out-stream-resume-m1m2`** (HEAD `64464d4`, PR #116 open)
- main HEAD: `fe9e9e4` (PR #115 머지 시점)
- alembic head **m34** (W3-out — `cd backend && uv run alembic upgrade head` 필수)
- backend **786 pass** (이전 735 + W3-out M1+M2 신규 ~50건) / frontend **249 pass + 0 skip** / lint·build clean
- pyright **0 errors / 0 warnings** / `.husky/pre-push` 자동 게이트 통과

## PR #116 (이번 사이클, 5 commits)

| Commit | 내용 |
|---|---|
| `a948cd8` | M1+M2 backend foundation (TTH 사일로 — jensen 구현, bezos 검증) |
| `d689255` | 1차 리뷰 BLOCKER + HIGH 일괄 (subscribe race / BrokerRegistry LRU cap / in-flight backpressure) |
| `8e83a69` | 2차 리뷰 H1+H2 + NIT (명시적 self-pop / retry_buffer cap / slow listener log) |
| `c3a2696` | N1 — m34 SQLite 결함 fix + 회귀 테스트 5건 |
| `64464d4` | /simplify 4건 — close_for_conversation 배선 / dict 중복 제거 / TraceStatus annotation |

총 22 files / +3235 / −212. 사용자 무영향 (publish-only, GET endpoint는 M3에서).

## 다음 작업 후보

1. **M3 GET resume endpoint** — `GET /api/conversations/{id}/stream?run_id=&last_event_id=` (~6h). 4 분기(live/replay/stale/interrupt-conflict). plan 파일 M3 섹션 참조
2. **M4 APScheduler lifecycle** — `registry.evict_expired` 60s interval job + shutdown hook (~3h)
3. **M5+M6 Frontend + integration** — lastEventId 추적 + `withAutoResume` + reconnect indicator + e2e 끊김 시뮬레이션 (~16h)
4. **phase-timeline-ui zinc 토큰화** (0.5일) — 별도 PR
5. **approval-card raw color 매핑** — emerald/blue/red/amber → primary/status-*
6. **Outdated deps**: FastAPI / Pydantic minor + cryptography / marshmallow / protobuf major
7. M-MCP2 / M-SKILL2

## PR #116에서 의도적으로 분리한 follow-up

- 🟠 cross-tenant LRU eviction → 인증 도입 PR과 동시 per-user/conversation sub-cap
- 🟠 trace_storage `append_events` O(N²) → PG `||` server-side concat (긴 turn 부하, 코드 주석에 명시)
- 🟡 `stream_agent_response` 9 params → `StreamCapture` dataclass + emit() `_Persistor` 추출
- 🟡 alembic `_now_default`/`_has_table` 8 마이그레이션 복붙 → `backend/alembic/_helpers.py` (drive-by chore)
- 🟡 `_SSE_HEADERS` 3 라우터(conversations/builder/assistant) 복붙 통합
- 🟡 `_StreamCtx` NamedTuple → frozen dataclass + `to_kwargs()`

## 알려진 이슈

- **base-ui SliderThumb script 경고**: mui/base-ui#4373 패치 대기
- **W6 trace 매핑**: m32 이전 row는 `linked_message_ids = NULL` (chronological 폴백)
- **mermaid 다크 톤**: PR #112 동적 전환 적용 — `themeVariables` oklch 매핑은 향후 트랙
- **Codex review proxy 다운**: `runpod.net` 404 — `/codex:review` 일시 사용 불가

## 코드 컨벤션 (W3-out 정착분 추가)

- SSE: `emit()` unique id `{msg_id}-{seq}` / `pillStatusFromAssistantUi(status.type)` / `orjson.dumps`
- **W3-out broker (PR #116)**: per-run `EventBroker` (ring deque 2000 + listeners 512 + close idempotent). `BrokerRegistry` process-local singleton. `_StreamCtx` + `_prepare_stream_context()`로 4 POST 핸들러 통일. `close_for_conversation` 진입 시 호출 (ghost broker 차단)
- **mid-stream persist**: `trace_storage.append_events` UPSERT + `_MAX_INFLIGHT_FLUSHES=4` backpressure + `retry_buffer` cap=5000 + `_safe_persist` retry → final flush 안전망
- **status enum**: `TraceStatus = Literal["streaming","completed","failed"]` (DB CHECK 제약)
- **m34 dialect 분기**: PG `CREATE INDEX CONCURRENTLY` + autocommit_block / SQLite `batch_alter_table` copy-and-move
- **테스트 누수 방지**: `tests/conftest.py` `_clear_event_broker_registry` autouse fixture (BrokerRegistry singleton)
- 비용/타입/HITL 양식/Markdown 통일 등 기존 컨벤션 그대로

## 검증

```bash
cd backend && uv run alembic upgrade head && uv run ruff check . && uv run pytest tests/ && uv run pyright
cd frontend && pnpm lint && pnpm test --run && pnpm build
```

## 핵심 파일 (다음 작업 진입점)

- W3-out broker: `backend/app/agent_runtime/event_broker.py` (EventBroker / BrokerRegistry / registry singleton)
- W3-out streaming: `backend/app/agent_runtime/streaming.py` (`emit` 클로저 + flush_buffer/retry_buffer/backpressure)
- W3-out persist: `backend/app/services/trace_storage.py` (`append_events`/`finalize_turn`/`record_turn` shim) + m34 마이그레이션
- W3-out routes: `backend/app/routers/conversations.py` (`_StreamCtx`, `_prepare_stream_context`, `_finalize_trace` dual-path, 4 POST + 추가될 GET)
- ADR: `docs/design-docs/adr-011-sse-stream-resume.md` (전체 설계 + M3-M6 마일스톤)
- Plan: `~/.claude/plans/1-ux-quirky-canyon.md` (구체 구현 가이드)
- 게이트: `.husky/pre-push`

## 새 트랙 시작 체크

1. PR #116 머지 확인 → `/sync` (main으로 전환 + pull)
2. `git checkout -b feature/w3-out-m3-get-stream` (또는 적절한 이름)
3. plan 파일 M3 섹션 참조 — GET endpoint 4 분기 구현
4. 작업마다 별도 commit, `git push`로 자동 게이트 통과
5. 마지막에 `gh pr create` (M3+M4 묶음 PR 권장)
