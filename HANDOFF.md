# 작업 인계 — W3-out 트랙 종료 + HiTL 미들웨어 통합 트랙 진입 준비

> 새 세션 진입: 본 파일 + `progress.txt` 마지막 4-5 섹션 + `~/.claude/plans/1-ux-quirky-canyon.md`.
> ⚠️ 다음 작업: **HiTL 표준 미들웨어 통합** — 선행 분석 PR 부터 시작.

## 마지막 상태

- 브랜치: **`main`** (HEAD `42eed7d`)
- W3-out 트랙 종료 + 6 PR 시리즈 모두 머지 완료
- backend 821 pass / pyright 0 / ruff clean / frontend 262 pass / lint·build clean

## W3-out 종료 후 PR 시리즈 (모두 머지)

| PR | 내용 |
|---|---|
| #119 | M6 E2E + ADR-011 + 트랙 종료 retrospective cleanup 5건 |
| #120 | frontend `event:stale` 처리 (silent → toast/indicator) |
| #121 | _StreamCtx 메서드 + integration seed 통합 + record_turn deprecate |
| #122 | stream_resume `get_owned_conversation` join — 1 round-trip 절감 |
| #123 | parse-sse 헤더 case fallback 일관성 (cosmetic) |
| #124 | `_resolve_agent_context` conv+agent 단일 join — 1 round-trip + SQL count 회귀 가드 |

## 다음 트랙 — HiTL 표준 미들웨어 통합

### 결정 (사용자 합의)

**표준 LangChain `HumanInTheLoopMiddleware` 로 마이그레이션.** 자체 구현 (`ask_user` special tool + `interrupt_on` dict 만 추출 + 자체 SSE INTERRUPT) 을 표준 미들웨어로 통합. deep agents 도입 의의 (도구별 정책 / SubAgent 상속 / 트리거 모드 자동 승인 / multi tool_call 일괄 검토) 활용.

### 현재 자체 구현 핵심 (마이그레이션 대상)

| 위치 | 책임 |
|---|---|
| `backend/app/agent_runtime/tools/ask_user.py` | LangGraph `interrupt()` 직접 호출하는 special tool |
| `backend/app/agent_runtime/streaming.py:331-367` | `GraphInterrupt` catch + SSE INTERRUPT event emit |
| `backend/app/routers/conversations.py:813-833` | `POST /messages/resume` 엔드포인트 |
| `backend/app/schemas/conversation.py:45-46` | `ResumeRequest{response: str|list|dict}` |
| `backend/app/agent_runtime/executor.py:477-493` | `interrupt_on` dict 만 추출 (미들웨어 객체는 안 만듦) |
| `backend/app/agent_runtime/middleware_registry.py:79-92, 419` | `human_in_the_loop` 등록되지만 인스턴스화 명시 제외 |

### 표준 미들웨어 vs 자체 차이

| 항목 | 자체 (현재) | 표준 (목표) |
|---|---|---|
| 트리거 방식 | `ask_user` special tool | 임의 tool 에 미들웨어 |
| Multi tool_call | 단일 tool 단위 | 한 AIMessage 의 모든 tool_call 묶음 |
| 액션 종류 | free-form `response` | approve / edit / reject / respond 4종 |
| Resume payload | `{response: ...}` | `{decisions: [{type, ...}]}` (count strict) |

### 4단계 마이그레이션 계획

1. **선행 분석 PR (코드 변경 X — 다음 세션 첫 작업)**:
   - `ask_user` 사용처 grep — 모든 호출 케이스 + free-form response 활용 정도
   - frontend HiTL UI 현재 구현 (`components/chat/approval/*` 추정) — 4 액션 적용 시 디자인 영향
   - Builder v3 (`creation_agent.py`, `builder_service`) HiTL 패턴 — 별도 graph 통합 비용
2. **Phase 1 — backend 인프라 (사용자 무영향)**: `executor.py` 가 `HumanInTheLoopMiddleware` 인스턴스를 deep agent 에 주입. dual-path 유지.
3. **Phase 2 — wire format 통합 (사용자 영향, dual-path 로 transition)**: ResumeRequest + INTERRUPT event payload 표준화. frontend UI 4 액션 확장.
4. **Phase 3-5**: `ask_user` 마이그레이션 → Builder v3 통합 → 자체 구현 제거.

### 채팅 진입점 매트릭스 (영향 범위)

| 진입점 | HiTL 가능 | 마이그레이션 영향 |
|---|---|---|
| POST `/messages` (send/resume/edit/regenerate) | ✅ | HIGH — 핵심 경로 |
| POST `/api/builder/.../messages(/resume)` | ✅ | 분리 검토 (Phase 4) |
| APScheduler trigger | ❌ | 명시 차단 (사용자 비동기) |

## W3-out 잔여 follow-up (선결 조건 / 트리거 도달 대기)

- 🟠 cross-tenant LRU sub-cap (인증 도입 PR 함께)
- 🟡 multi-worker (Redis pub/sub 또는 sticky routing) — broker registry 재설계
- 🟡 `evict_expired` dirty flag — multi-worker 결정 후
- 🟡 `events_chunks` 별도 테이블 — turn 5000+ events 트리거 도달 시

## TASKS.md 미완료 (HiTL 외)

- Phase 6: E2E 시나리오 검증 / 접근성·키보드·성능
- Phase 14: 미들웨어 프리셋 / 드래그앤드롭 / Provider 자동 감지

## 핵심 파일 (새 세션 진입 시 참조)

- HiTL 코드: `backend/app/agent_runtime/{tools/ask_user.py,streaming.py,executor.py,middleware_registry.py,event_names.py}`
- HiTL 라우트: `backend/app/routers/conversations.py:813-833` (`/messages/resume`)
- frontend HiTL: `frontend/src/lib/types/index.ts:InterruptPayload`, `frontend/src/lib/chat/use-chat-runtime.ts:case 'interrupt'`, `frontend/src/components/chat/approval/*`
- Standard 미들웨어 참조: `backend/.venv/lib/python3.13/site-packages/langchain/agents/middleware/human_in_the_loop.py`, `backend/.venv/lib/python3.13/site-packages/deepagents/graph.py`

## 새 트랙 시작 체크

1. 새 브랜치: `feature/hitl-analysis` (분석 PR — 코드 변경 X, 결과 문서만)
2. 분석 결과로 Phase 1 PR 사이즈 정확히 결정
3. plan 또는 ADR 작성 (대규모 트랙이라 권장)
