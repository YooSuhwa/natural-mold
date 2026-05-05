# 작업 인계 — W3-out M6 완료 + 트랙 종료 retrospective

> 새 세션 진입: 본 파일 + `progress.txt` 마지막 4-5 섹션 + `~/.claude/plans/1-ux-quirky-canyon.md`.
> 설계: `docs/design-docs/adr-011-sse-stream-resume.md` (M1-M6 결정 1-11 반영 완료).
> ⚠️ 첫 작업: PR 생성 → 머지 → main 으로 sync.

## 마지막 상태

- 브랜치: **`feature/w3-out-m6-integration`** (4 커밋, PR 미생성)
- 직전 main HEAD: `66ccedd` (PR #118 머지 — M5 frontend)
- backend **819 pass** / pyright 0 / ruff clean / frontend **262 tests** / lint·build clean
- 사용자 무영향 (test + docs + dead constant rename + docstring 정리만)

## W3-out 전체 진행

| 마일스톤 | 상태 | PR |
|---|---|---|
| M1 EventBroker primitive | ✅ | #116 |
| M2 streaming.py + m34 partial flush | ✅ | #116 |
| M3 GET /stream endpoint | ✅ | #117 |
| M4 lifecycle + APScheduler | ✅ | #117 |
| M5 frontend auto-resume | ✅ | #118 |
| **M6 E2E + ADR + retrospective** | ✅ | (현 PR) |

## 이번 사이클 4 커밋

| sha | 내용 |
|---|---|
| `1e38176` | M6 E2E 4 시나리오 (live attach / replay-only / stale crash sim / interrupt pending) — 진짜 POST 핸들러 통과 + ADR-011 M1+M2 시점 작성본을 M3-M5 결정까지 확장 (1-9 → 1-11) |
| `a91bc98` | 1차 리뷰 fix (race-free invariant 주석, scenario C 의 `_clear()` 추가, `get_running_loop()`, ADR 상태 갱신, dead `contextlib` 제거) |
| `b12ca15` | /simplify (M6 단독) — `event_names` 상수 도입, `captured["run_id"]` 제거, `_drive_post_to_completion` 헬퍼, scenario A task cleanup `shield+wait_for+cancel` 패턴 |
| `a325697` | /simplify (트랙 종료 retrospective) — B1 `event_names.TOOL_CALL/TOOL_RESULT` (dead) → `TOOL_CALL_START/TOOL_CALL_RESULT` (wire format 정합), L1 broker capacity docstring 갱신, L2 `resume_forbidden` intentional dead 표지, N1 `_finalize_trace` dead default 제거, H2 ADR retry 명세 (5회/[1,2,4,8]s → 3회/[500,1500,4000]ms 로 구현 정합) |

## E2E 실서비스 검증 결과 (5/5 본 세션)

UI happy path (잡담 친구 agent, Sonnet 4.6) — agent-browser CLI:
- 짧은 응답: 18.5k/1.0k tokens, $0.04 ✅
- 긴 응답 (8000자 SF + Plan middleware): 50.8k/9.1k tokens, 80s, $0.20 ✅

Backend GET `/stream` runtime — curl:
- unknown run_id / unknown conv_id → 동일 `404 RESUME_NOT_FOUND` (oracle 통일) ✅
- 실제 완료 turn run_id → `200 X-Resume-Mode: replay`, 206 events SSE ✅
- B1 fix runtime: `tool_call_start`/`tool_call_result` 정상 emit ✅

Resume indicator 시연: agent-browser 자동화 한계 (withAutoResume 는 같은 generator chain). M6 backend 통합 (시나리오 A live attach) + M5 manual e2e 가 커버.

스크린샷: `/tmp/w3out-e2e/{01,02}-*.png`.

## 의도적 follow-up (M6 PR 범위 초과 — 별도 PR)

- 🟠 **frontend `event: stale` 처리** — `SSEEventType` union 에 `'stale'` 추가 + `consumeStream` 의 `case 'stale':` 에서 toast/indicator 노출. 현재 silent stream 종료 (자동 retry 만 멈춤).
- 🟡 `_StreamCtx.as_stream_kwargs()` + `finalize_callback()` — 4 POST 핸들러 ctx 보일러플레이트 ~30 라인 절약.
- 🟡 `_seed_conv` (`test_stream_resume.py`) ↔ `_seed` (`test_broker_dual_write.py`) 통합 → conftest helper.
- 🟡 `_resolve_agent_context` round-trip 4-6회 통합 (latency 측정 선행).
- 🟡 `record_turn` deprecate 검토 (`finalize_turn` 경로 전환 — 12 legacy 호출자).
- 🟡 turn 당 events 5000+ 시 `events_chunks` 별도 테이블.
- 🟡 multi-worker — Redis pub/sub 또는 sticky routing (현재 workers=1).
- 🟠 cross-tenant LRU sub-cap (인증 도입 PR 과 함께).
- 🟢 `parse-sse.ts` 헤더 case fallback 비대칭 (실질 위험 낮음).

## 알려진 한계

- backend 통째 죽으면 broker 같이 사망 → DB replay 만 받고 종료. transient drop (Wi-Fi 토글) 만 진짜 live attach.
- workers=1 가정.
- M6 시나리오 C E2E 는 `_finalize_trace` no-op patch 로 crash 시뮬 — 실제 SIGKILL 회귀는 M5 manual e2e 가 보장.

## 핵심 파일 (참조)

- broker: `backend/app/agent_runtime/event_broker.py`
- routes: `backend/app/routers/conversations.py` (`stream_resume`, `_prepare_stream_context`, `_finalize_trace`)
- streaming: `backend/app/agent_runtime/streaming.py` (`stream_agent_response`)
- event names: `backend/app/agent_runtime/event_names.py` (single source of truth)
- broker unit tests: `backend/tests/agent_runtime/test_event_broker.py` (21)
- stream resume tests: `backend/tests/integration/test_stream_resume.py` (router-단위 17 + M6 E2E 4 = 21)
- frontend: `frontend/src/lib/sse/{with-auto-resume,stream-resume-attach,parse-sse}.ts`, `frontend/src/components/chat/reconnect-indicator.tsx`
- ADR: `docs/design-docs/adr-011-sse-stream-resume.md`
- Plan: `~/.claude/plans/1-ux-quirky-canyon.md`

## 새 트랙 시작 체크

1. PR 생성 → 머지 → `/sync`
2. `tasks/archive/HANDOFF-w3-out-m6.md` 로 본 파일 보관
3. 다음 트랙 SPEC / plan 진입 (의도적 follow-up 중 우선순위 결정)
