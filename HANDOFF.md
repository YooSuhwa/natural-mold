# 작업 인계 — HiTL 미들웨어 마이그레이션 Phase 2 진입

> 새 세션 진입: 본 파일 + **`docs/design-docs/adr-012-hitl-middleware-migration.md`** (필독, 5 Phase 계획).
> 이전 트랙: Phase 1 (Backend 명시 인스턴스화) 완료 — 본 PR.
> ⚠️ 다음 작업: ADR-012 의 **Phase 2 — Wire format 통합 (사용자 영향, ~400 라인)**.

## 마지막 상태

- 브랜치: **`feature/hitl-phase1-middleware-instance`** (PR 본 트랙)
- 메인: `main` HEAD `750d587` (Phase 0 머지 완료)
- backend 826 pass (821 + 신규 5) / pyright 0 / ruff clean / alembic clean
- frontend 미수정 (Phase 1 사용자 무영향)

## Phase 1 변경 요약 (본 PR)

### 코드
- `backend/app/agent_runtime/executor.py` (+37):
  - `from langchain.agents.middleware import HumanInTheLoopMiddleware` 직접 import
  - `_prepare_agent` docstring: `include_ask_user=False` = 트리거 모드 indicator 명시
  - **트리거 모드 강제 차단**: `include_ask_user=False` 일 때 `interrupt_on = None`
  - **명시 인스턴스화**: `interrupt_on` 이 truthy 면 `HumanInTheLoopMiddleware(interrupt_on=...)` 인스턴스를 `middleware` list 에 append
  - `build_agent(..., interrupt_on=None, ...)` — deepagents 자동 주입 회피 (자동 vs 명시 중복 방지)
- `backend/app/agent_runtime/middleware_registry.py` (+12, 주석 위주): `DEEPAGENT_BUILTIN_TYPES.human_in_the_loop` 의 의도 (자동 주입 회피 + executor 명시 인스턴스화 경로) 문서화
- `backend/tests/test_hitl_middleware.py` (신규, ~330): 5 회귀 가드 테스트
  - `test_hitl_middleware_instance_injected_when_interrupt_on_provided`
  - `test_hitl_middleware_not_injected_in_trigger_mode`
  - `test_hitl_middleware_per_tool_policy_applied`
  - `test_hitl_middleware_auto_extraction_from_write_keywords`
  - `test_deepagents_interrupt_on_param_is_none_when_explicit_instance`

### Done-when 충족
- 표준 미들웨어 인스턴스가 deep agent 에 명시적으로 주입됨 ✅
- SSE wire 는 자체 형식 유지 (streaming.py 미수정, routers/conversations.py 미수정) ✅
- 트리거 모드 자동 승인 (interrupt_on 차단) ✅
- 회귀 0 — 기존 821 + 신규 5 = 826 PASS ✅

### 베조스 리뷰 (verdict: APPROVE)
- 회귀 위험 0건. 수정금지 영역 0 라인.
- 테스트 품질 4/5 (langchain internal 속성 fallback 으로 brittle 완화, 주석 명시).

## Phase 2 진입 사전 정보

| Phase | 내용 | 사이즈 | 사용자 영향 |
|---|---|---|---|
| ✅ Phase 0 | 선행 분석 + ADR-012 (PR #126) | ~600줄 doc | X |
| ✅ Phase 1 | Backend 명시 인스턴스화 (본 PR) | ~150 라인 | X |
| **Phase 2** | **Wire format 통합 — INTERRUPT event + ResumeRequest 표준화. frontend multi-action 큐** | **~400 라인** | **O (dual-path transition)** |
| Phase 3 | Transition 종료 — dual-path 제거 | ~80 라인 | X |
| Phase 4 | `ask_user` 검토 (옵션) | ~30 라인 | 회귀 위험 |
| Phase 5 | Builder v3 wire format 통일 (옵션) | ~150 라인 | O |

## Phase 2 구체 작업 (다음 세션 첫 PR)

**브랜치**: `feature/hitl-phase2-wire-format` (Phase 1 머지 후 main 에서 분기)

**Backend**:
- `backend/app/schemas/conversation.py:45-46`: `ResumeRequest` 에 `decisions: list[Decision]` 필드 추가. `response: str | list[str] | dict | None` 은 deprecated 로 유지 (dual-shape transition).
- `backend/app/routers/conversations.py:813-833`: `decisions` → `Command(resume={"decisions": [...]})`. `response` 들어오면 단일 respond decision 으로 변환 (transition).
- `backend/app/agent_runtime/streaming.py:331-367`: `GraphInterrupt` catch 시 표준 `{action_requests, review_configs}` 형식 emit. 기존 자체 형식도 dual emit (transition window).

**Frontend**:
- `frontend/src/lib/types/index.ts:InterruptPayload`: 표준 + 기존 두 형식 union
- `frontend/src/lib/chat/use-chat-runtime.ts`: `case 'interrupt'` 표준 payload 처리 (multi-action 큐)
- `frontend/src/lib/sse/stream-resume.ts`: `{decisions: [...]}` 형식 송신
- `HiTLContext` / `useHiTL`: 배열 처리 + 어댑터
- `messages/ko.json`: `chat.approval.respond`, `chat.approval.allActionsCompleted` 등 라벨 추가

**Done-when**: 표준 + 기존 wire 양쪽 작동, frontend 4 액션 + multi-action 큐 지원, 회귀 0.

**검증**:
```
cd backend && uv run alembic upgrade head && uv run ruff check . && uv run pytest tests/ && uv run pyright app/ tests/
cd frontend && pnpm lint && pnpm test --run && pnpm build
```

## 핵심 파일 (Phase 2 수정 대상)

- `backend/app/schemas/conversation.py:45-46` — ResumeRequest dual-shape
- `backend/app/routers/conversations.py:813-833` — resume_message 핸들러
- `backend/app/agent_runtime/streaming.py:331-367` — INTERRUPT event dual emit
- `frontend/src/lib/types/index.ts` — InterruptPayload union
- `frontend/src/lib/chat/use-chat-runtime.ts` — multi-action 큐
- `frontend/src/lib/sse/stream-resume.ts` — decisions 송신
- `frontend/src/components/chat/{user-input-ui,approval/*}.tsx` — wire 어댑터 (컴포넌트 자체는 4 액션 이미 지원)

## 참조 (Phase 2 변경 X)

- `backend/app/agent_runtime/executor.py` — Phase 1 에서 명시 인스턴스화 완료, Phase 2 미수정
- `backend/app/agent_runtime/middleware_registry.py` — Phase 1 에서 주석 갱신, Phase 2 미수정
- `backend/app/agent_runtime/tools/ask_user.py` — Phase 4 까지 보존
- `backend/app/agent_runtime/builder_v3/graph.py` — Phase 5 까지 변경 X

## 핵심 제약 (Phase 1 산출물)

- `_prepare_agent` 의 트리거 indicator 는 `include_ask_user=False`. Phase 2 에서 신규 호출 경로 추가 시 동일 indicator 유지 또는 명시적 `is_trigger` 플래그 도입 검토
- 신규 테스트 `test_hitl_middleware.py` 는 langchain 의 `tool_configs`/`interrupt_on` 속성에 fallback 의존 — langchain 1.x 마이너 업그레이드 시 회귀 가능. **Phase 2 PR 에서도 해당 테스트 보존**
- `build_agent(interrupt_on=None)` 고정 — 외부 호출자가 deepagents 자동 주입 의존 시 깨짐. backend 내부 호출은 `_prepare_agent` 단일이므로 안전, 외부 노출 시 재검토

## W3-out 트랙 잔여 follow-up (선결 / 트리거 도달 대기)

- 🟠 cross-tenant LRU sub-cap (인증 도입 PR 함께)
- 🟡 multi-worker (Redis pub/sub) — broker registry 재설계
- 🟡 `evict_expired` dirty flag — multi-worker 결정 후
- 🟡 `events_chunks` 별도 테이블 — turn 5000+ events 도달 시

## 새 트랙 시작 체크 (Phase 2)

1. 본 PR 머지 후 `git checkout main && git pull`
2. `feature/hitl-phase2-wire-format` 신규 브랜치
3. ADR-012 §Phase 2 정독 + 본 HANDOFF Phase 2 작업 항목 정독
4. backend 부터 시작 (schemas → router → streaming) → frontend (types → runtime → adapters)
5. Dual-path 회귀 테스트 — 표준 + 기존 wire 양쪽 시나리오
6. PR 단일 — backend + frontend 통합, dual-path transition 명시
