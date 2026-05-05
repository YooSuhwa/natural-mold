# 작업 인계 — HiTL Phase 3 진입

> 새 세션 첫 행동: 본 파일 + `docs/design-docs/adr-012-hitl-middleware-migration.md` (5 Phase 계획) + `docs/exec-plans/active/hitl-phase2-contract.md` (wire 계약, Phase 3에서 legacy 제거 시 참조).

## 마지막 상태

- 브랜치: `feature/hitl-phase2-wire-format` (커밋 미수행 — 사용자 승인 대기)
- 메인: `main` HEAD `750d587`
- backend: 849 PASS / pyright 0/0 / ruff clean / alembic OK
- frontend: 276 PASS (47 files) / lint clean / build PASS (16 routes)

## Phase 2 완료 (본 PR)

자체 HiTL wire → LangChain `HumanInTheLoopMiddleware` 표준으로 dual-path transition.

**Backend (3)**: `schemas/conversation.py` (Decision + ResumeRequest dual-shape), `routers/conversations.py:resume_message` (legacy→respond decision 변환), `agent_runtime/streaming.py` (정상 분기 dual emit / fallback은 legacy chunk 단독).

**Frontend (5 + page 2)**: `lib/types/index.ts` (InterruptPayload union), `lib/chat/use-chat-runtime.ts` (`'action_requests' in data` 분기 + dedup), `lib/sse/stream-resume.ts` (`streamResumeDecisions` 신규), `lib/chat/hitl-context.ts` (onResumeDecisions 추가), `messages/ko.json` (5 라벨), 페이지 2 (HiTLContext useMemo 와이어).

**테스트 신규 (3)**: `backend/tests/test_hitl_phase2_wire.py` (23), `frontend/.../stream-resume.test.ts` (6), `use-chat-runtime-hitl.test.tsx` (9).

**문서 신규**: `docs/exec-plans/active/hitl-phase2-contract.md` (508줄) — wire 계약 단일 진실 공급원.

**/simplify 적용**: fallback 분기 단순화 (legacy 단독 emit), frontend 분기 평탄화, chat page `useMemo`. 5건 적용 / 4건 Skip (Phase 3 자연 소멸).

## Phase 3 진입 (다음 세션)

**브랜치**: `feature/hitl-phase3-transition-end` (Phase 2 머지 후 main 분기)

**작업 (~80 라인, 사용자 무영향 — clean break)**:
- Backend: `ResumeRequest.response` 필드 제거 / `resume_message` legacy 분기 삭제 / `streaming.py` legacy chunk emit 삭제 (정상 분기에서도 표준 단독)
- Frontend: `InterruptPayload` union → 표준 단일 / `streamResume` 삭제 (`streamResumeDecisions`만) / `hitl-context.ts` `onResume` 어댑터 삭제 / `use-chat-runtime.ts` 분기/dedup 제거 (legacy 없으므로 단일 경로) / legacy ko 라벨 정리
- 테스트 정리: legacy 시나리오 삭제, 표준만 보존. `test_hitl_phase2_wire.py` → `test_hitl_wire.py` rename 검토

**검증**:
```
cd backend && uv run alembic upgrade head && uv run ruff check . && uv run pytest tests/ && uv run pyright app/ tests/
cd frontend && pnpm lint && pnpm test --run && pnpm build
```

## 보존 영역 (Phase 3에서도 수정 X)
- `backend/app/agent_runtime/middleware_registry.py` (Phase 1)
- `backend/app/agent_runtime/executor.py:_prepare_agent` (Phase 1, 트리거 차단)
- `backend/tests/test_hitl_middleware.py` (Phase 1, 5 회귀 가드)
- `backend/app/agent_runtime/tools/ask_user.py` (Phase 4까지)
- `backend/app/agent_runtime/builder_v3/**` (Phase 5까지, 자체 native interrupt)

## 핵심 제약
- 트리거 모드 indicator: `_prepare_agent(include_ask_user=False)` (Phase 1). 신규 호출 경로 추가 시 동일 indicator 유지.
- Phase 2 회귀 가드는 langchain의 `tool_configs`/`interrupt_on` 속성 fallback에 의존 — langchain 1.x 마이너 업그레이드 시 회귀 가능. 보존 필수.
- contract `docs/exec-plans/active/hitl-phase2-contract.md`는 Phase 3 진입 시 `completed/`로 이동 + Phase 3 contract 신규 작성 검토.

## W3-out 잔여 follow-up (트리거 도달 대기)
- 🟠 cross-tenant LRU sub-cap (인증 도입 PR 함께)
- 🟡 multi-worker (Redis pub/sub) — broker registry 재설계
- 🟡 `evict_expired` dirty flag — multi-worker 후
- 🟡 `events_chunks` 별도 테이블 — turn 5000+ events 도달 시
