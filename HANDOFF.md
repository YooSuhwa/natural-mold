# 작업 인계 — W3-out M6 완료

> 새 세션 진입: 본 파일 + `progress.txt` 마지막 4-5 섹션 + `~/.claude/plans/1-ux-quirky-canyon.md` (마무리).
> 설계: `docs/design-docs/adr-011-sse-stream-resume.md` (M3-M5 결정 반영 완료).
> ⚠️ 첫 작업: PR 머지 여부 확인 → main 이면 W3-out 트랙 종료, 다음 트랙 선택.

## 마지막 상태

- 브랜치: **`feature/w3-out-m6-integration`** (PR 미생성)
- 직전 main HEAD: `66ccedd` (PR #118 머지)
- backend **819 pass** / pyright 0 / ruff clean / frontend 262 tests / lint·build clean
- 사용자 무영향 (backend test + docs 만)

## W3-out 전체 진행

| 마일스톤 | 상태 | PR |
|---|---|---|
| M1 EventBroker primitive | ✅ | #116 |
| M2 streaming.py + m34 partial flush | ✅ | #116 |
| M3 GET /stream endpoint | ✅ | #117 |
| M4 lifecycle + APScheduler | ✅ | #117 |
| M5 frontend auto-resume + 회귀 fix | ✅ | #118 |
| **M6 통합 테스트 + ADR-011** | ✅ | (현 PR) |

## 이번 사이클 변경

| 파일 | 내용 |
|---|---|
| `backend/tests/integration/test_stream_resume.py` | E2E 4 시나리오 (A live attach / B replay-only / C stale / D interrupt) — 진짜 POST 핸들러 통과시키며 broker 등록 + partial flush + finalize_turn cross-handler invariant 검증. `async_session` monkeypatch + `_finalize_trace` no-op patch (C) + `_make_executor_simulator` 헬퍼 도입 |
| `docs/design-docs/adr-011-sse-stream-resume.md` | M1+M2 시점 작성본 → M3-M5 결정 사항 반영 (보안 oracle 통일 / `slice_events_after` 공유 / `event_names.py` 단일 source / shutdown 순서 / withAutoResume HOF / 알려진 한계 명시). 결정 1-9 → 1-11 확장 |
| `HANDOFF.md` | M6 진입 준비 → M6 완료 |
| `tasks/archive/HANDOFF-w3-out-m3-m5.md` | 직전 사이클 HANDOFF 보관 |

## 검증

```bash
cd backend && uv run alembic upgrade head && uv run ruff check . && uv run pytest tests/ && uv run pyright
cd frontend && pnpm lint && pnpm test --run && pnpm build
```

## 의도적 follow-up (W3-out 외)

- 🟠 cross-tenant LRU sub-cap (인증 도입 PR 과 함께)
- 🟡 multi-worker — Redis pub/sub 또는 sticky routing (현재 workers=1)
- 🟡 `get_conversation` + `get_agent_for_user` schema-level join
- 🟡 `evict_expired` dirty flag
- 🟡 turn 당 events 5000+ 시 `events_chunks` 별도 테이블
- 🟡 `record_turn` deprecate 검토 (`finalize_turn` 경로 전환 — 12 legacy 호출자)

## 알려진 한계 (plan 명세대로)

- backend 통째 죽으면 broker 같이 죽어 in-flight LangGraph turn 사라짐 → DB replay 만 받고 종료. transient network drop (Wi-Fi 토글) 은 broker 살아있어 X-Resume-Mode: live 로 진짜 이어짐.
- workers=1 가정. multi-worker 는 후속 트랙.
- M6 시나리오 C E2E 는 `_finalize_trace` no-op monkeypatch 로 crash 시뮬레이션 — 실제 SIGKILL 회귀는 수동 e2e (M5 머지 시점 통과) 가 보장.

## 핵심 파일 (완료된 인프라)

- broker: `backend/app/agent_runtime/event_broker.py` (`BrokerRegistry`, `EventBroker`, `slice_events_after`)
- routes: `backend/app/routers/conversations.py` (`stream_resume`, `_replay_resume_generator`, `_log_resume_reject`, `_prepare_stream_context`)
- broker unit tests: `backend/tests/agent_runtime/test_event_broker.py` (21 tests)
- stream resume tests: `backend/tests/integration/test_stream_resume.py` (router-단위 17 + M6 E2E 4 = 21)
- frontend: `frontend/src/lib/sse/with-auto-resume.ts`, `stream-resume-attach.ts`, `parse-sse.ts`, `frontend/src/components/chat/reconnect-indicator.tsx`
- ADR: `docs/design-docs/adr-011-sse-stream-resume.md`
- Plan: `~/.claude/plans/1-ux-quirky-canyon.md`

## 새 트랙 시작 체크

1. PR #(현재 미생성) 머지 확인 + main HEAD 갱신
2. `tasks/archive/HANDOFF-w3-out-m6.md` 로 본 파일 보관 (선택)
3. 다음 트랙 SPEC / plan 진입
