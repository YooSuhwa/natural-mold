# 작업 인계 — HiTL 미들웨어 마이그레이션 Phase 1 구현 진입

> 새 세션 진입: 본 파일 + **`docs/design-docs/adr-012-hitl-middleware-migration.md`** (필독, 5 Phase 계획).
> ⚠️ 첫 작업: ADR-012 의 **Phase 1 — Backend 인프라 (사용자 무영향, ~150 라인)** 구현.

## 마지막 상태

- 브랜치: **`main`** (HEAD `db04b73` — PR #125 머지 후)
- W3-out 트랙 종료 + 6 PR 시리즈 머지 완료 (#119–#124)
- HiTL 선행 분석 + ADR-012 작성 완료 (본 PR — feature/hitl-analysis-and-plan)
- backend 821 pass / pyright 0 / ruff clean / frontend 262 pass / lint·build clean

## 분석 결과 핵심 (ADR-012 정독 권장)

### 결정 1 — 메인 채팅만 표준 `HumanInTheLoopMiddleware` 로 마이그레이션

자체 구현 (`ask_user` + 자체 INTERRUPT + ResumeRequest{response}) → 표준 미들웨어. **트리거 모드 자동 승인 / 도구별 정책 / multi tool_call 일괄 검토** 가치 활용.

### 결정 2 — Builder v3 는 자체 패턴 유지 (직교 관계)

Builder v3 는 8-phase deterministic state machine (`backend/app/agent_runtime/builder_v3/graph.py`). 노드가 `interrupt()` 직접 호출하는 propose+wait 분리 패턴 — tool_call interrupt 메타포에 안 맞음. 메인 채팅과 wire format 만 선택적 통일 (Phase 5).

### 결정 3 — Frontend HiTL UI 4 액션 이미 구현됨

`UserInputUI` (respond) + `ApprovalCard` (approve/reject/edit) 모두 존재. Phase 2 의 frontend 작업은 신규 컴포넌트가 아닌 **wire 어댑터 + multi-action 큐 처리**.

### 결정 4 — `ask_user` 도구 보존 (옵션 A)

`ask_user` 그대로 유지 + `interrupt_on={"ask_user": True}` 로 표준 미들웨어가 wrap. LLM prompt 회귀 위험 0. Phase 4 에서 retire 검토.

## 5 Phase 계획 (ADR-012 §마이그레이션 단계)

| Phase | 내용 | 사이즈 | 사용자 영향 |
|---|---|---|---|
| Phase 0 | 선행 분석 + ADR-012 (본 PR) | ~600줄 doc | X |
| **Phase 1** | **Backend 인프라 — `executor.py` 가 `HumanInTheLoopMiddleware` 인스턴스를 deep agent 에 주입. dual-path 유지** | **~150 라인** | **X** |
| Phase 2 | Wire format 통합 — INTERRUPT event + ResumeRequest 표준화. frontend multi-action 큐 | ~400 라인 | O (dual-path transition) |
| Phase 3 | Transition 종료 — dual-path 제거 | ~80 라인 | X |
| Phase 4 | `ask_user` 검토 (옵션) | ~30 라인 | 회귀 위험 |
| Phase 5 | Builder v3 wire format 통일 (옵션) | ~150 라인 | O |

## Phase 1 구체 작업 (다음 세션 첫 PR)

**브랜치**: `feature/hitl-phase1-middleware-instance`

**파일**:
- `backend/app/agent_runtime/executor.py` (~30 라인): `interrupt_on` dict → `HumanInTheLoopMiddleware(interrupt_on=...)` 인스턴스화. deep agent 에 추가. invoke (트리거) 모드는 `interrupt_on` config 강제 override (모두 `False`).
- `backend/app/agent_runtime/middleware_registry.py` (~10 라인): `human_in_the_loop` 제외 목록 정리 — 미들웨어 정상 인스턴스화 경로.
- `backend/tests/test_hitl_middleware.py` (신규, ~100 라인): 도구별 `interrupt_on` 적용 / 트리거 모드 자동 승인 회귀 가드.

**Done-when**: 표준 미들웨어 인스턴스가 deep agent 에 주입되지만 SSE wire 는 자체 형식 유지. backend 게이트 통과 + 기존 821 tests 회귀 0 + 신규 테스트 통과.

**검증**: `cd backend && uv run alembic upgrade head && uv run ruff check . && uv run pytest tests/ && uv run pyright app/ tests/`

## 핵심 파일 (수정 대상 — Phase 1)

- `backend/app/agent_runtime/executor.py:477-493` — `interrupt_on` dict 추출 부분 (현재 미들웨어 인스턴스화 X)
- `backend/app/agent_runtime/middleware_registry.py:79-92, 419` — `human_in_the_loop` 등록 + 제외 처리
- `backend/.venv/lib/python3.13/site-packages/langchain/agents/middleware/human_in_the_loop.py` — 표준 미들웨어 source 참조
- `backend/.venv/lib/python3.13/site-packages/deepagents/graph.py` — `create_deep_agent(interrupt_on=...)` 자동 주입 위치

## 참조 (변경 X — Phase 2 이후 영향)

- `backend/app/agent_runtime/tools/ask_user.py` — Phase 4 까지 보존
- `backend/app/agent_runtime/streaming.py:331-367` — Phase 2 에서 표준 형식 dual emit
- `backend/app/routers/conversations.py:813-833` — Phase 2 에서 ResumeRequest dual-shape
- `backend/app/schemas/conversation.py:45-46` — Phase 2 에서 `decisions` 필드 추가
- `backend/app/agent_runtime/builder_v3/graph.py` — Phase 5 까지 변경 X
- `frontend/src/lib/types/index.ts:InterruptPayload` — Phase 2
- `frontend/src/lib/chat/use-chat-runtime.ts:case 'interrupt'` — Phase 2 multi-action 큐
- `frontend/src/components/chat/{user-input-ui,approval/*}.tsx` — Phase 2 wire 어댑터 (컴포넌트 자체는 4 액션 이미 지원)

## W3-out 트랙 잔여 follow-up (선결 / 트리거 도달 대기)

- 🟠 cross-tenant LRU sub-cap (인증 도입 PR 함께)
- 🟡 multi-worker (Redis pub/sub) — broker registry 재설계
- 🟡 `evict_expired` dirty flag — multi-worker 결정 후
- 🟡 `events_chunks` 별도 테이블 — turn 5000+ events 도달 시

## 새 트랙 시작 체크 (Phase 1)

1. `git status` 클린 + main HEAD 확인
2. `feature/hitl-phase1-middleware-instance` 신규 브랜치
3. ADR-012 §Phase 1 정독
4. `executor.py:477-493` 의 현재 `interrupt_on` 처리 코드 확인 후 표준 미들웨어 wrap
5. 신규 단위 테스트 작성 (도구별 적용 + 트리거 모드 차단)
6. PR 단일 — backend test + 코드 만, 사용자 무영향
