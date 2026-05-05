# 작업 인계 — HiTL Phase 4 진입 (옵션, ask_user 검토)

> 새 세션 첫 행동: 본 파일 + `docs/design-docs/adr-012-hitl-middleware-migration.md` (Phase 4 §) + `docs/exec-plans/completed/hitl-phase2-contract.md` (transition 계약 참조).

## 마지막 상태

- 브랜치: `feature/hitl-phase3-transition-end` (main `be5a735`에서 분기, **커밋 미수행 — 사용자 승인 대기**)
- backend: **847 PASS** / pyright 0/0 / ruff clean / alembic OK
- frontend: **270 PASS** (47 files) / lint clean / build PASS (16 routes)
- legacy 코드 잔재 grep: 0건

## Phase 3 + /simplify 완료

dual-path transition window 종료 → **legacy wire format 완전 제거**, 사용자 무영향 clean break + 코드 품질 정리.

**Backend (4 파일)**:
- `schemas/conversation.py`: `ResumeRequest{decisions: list[Decision]}` 단일 필드
- `routers/conversations.py:resume_message`: `_legacy_response_to_decisions` 헬퍼 + dual 분기 제거
- `agent_runtime/streaming.py`: 표준 chunk 단독 emit. `_interrupt_to_standard_chunk` 헬퍼 — 자체 ask_user native interrupt를 표준 `respond` action으로 어댑트. fallback은 빈 표준 chunk.
- `agent_runtime/executor.py`: ADR-012 §1 옵션 A 부분 활성 — `interrupt_on`에 `ask_user` 자동 등록 (human_in_the_loop 미들웨어 활성 시)
- `tests/test_hitl_phase2_wire.py` 삭제 → `tests/test_hitl_wire.py` (21건)

**Frontend (12 파일, 순 -615 라인)**:
- `lib/types/index.ts`: `LegacyInterruptPayload` 제거
- `lib/sse/stream-resume.ts`: `streamResume` 제거
- `lib/chat/hitl-context.ts`: `onResume` 어댑터 제거
- `lib/chat/use-chat-runtime.ts`: legacy 분기/dedup 제거. builder 호환 어댑터(첫 decision의 message 추출) 내부 흡수
- Tool UI 5곳: `onResumeDecisions` 마이그레이션. `approval-card.tsx`는 `toDecision()` switch 헬퍼 + tool_name 부재 시 edit 거절. `image-generation-ui.tsx`는 `submitChoice()` 헬퍼로 copy-paste 통합.
- chat 페이지 2곳: HiTLContext value 정리

**문서**: `hitl-phase2-contract.md` → `completed/`

## Phase 4 진입 (옵션, ~30 라인)

브랜치: `feature/hitl-phase4-ask-user-review`

작업 (ADR-012 "Phase 4" §):
- ask_user 도구 의존성 평가 (LLM prompt 영향)
- 단순화 또는 retire 결정. 보존 시 description 조정.
- 마이그레이션 시 → ask_user.py 표준 미들웨어 패턴 전환 + `streaming.py` 어댑터 분기 제거

## Phase 3 후속 (별도 PR 권장)

1. **빈 fallback chunk UX**: backend가 `aget_state` 실패 시 emit하는 `{action_requests:[],review_configs:[]}`은 frontend chat 페이지에서 미노출. 명시 fallback UI 핸들러 필요 시 chat 페이지에 `onStandardInterrupt` 주입.
2. **builder 어댑터 추출**: `use-chat-runtime.ts` 내부 흡수된 builder 호환 코드 → `lib/chat/builder-resume-adapter.ts` 분리.
3. **Decision 매퍼 통합**: 인라인 객체 리터럴 11곳 → `lib/chat/decision-mappers.ts` (toApprove/toReject/toEdit/toRespond).

## 보존 영역 (Phase 4에서도 수정 X)
- `agent_runtime/middleware_registry.py` (Phase 1)
- `tests/test_hitl_middleware.py` (Phase 1, 5건)
- `agent_runtime/builder_v3/**` (Phase 5까지)

## 핵심 제약
- 트리거 모드 indicator: `_prepare_agent(include_ask_user=False)` (Phase 1) — 보존
- `streaming.py:_interrupt_to_standard_chunk`의 ask_user 분기는 ADR §1 옵션 A wrap이 정상 작동하면 도달 X — 안전망 (Phase 4 마이그레이션 시 분기 제거 가능)
- ask_user 자동 interrupt_on 등록은 사용자가 human_in_the_loop 미들웨어 활성화한 경우만 — 기본 비활성 에이전트 영향 X

## 검증 명령
```
cd backend && uv run alembic upgrade head && uv run ruff check . && uv run pytest tests/ && uv run pyright app/ tests/
cd frontend && pnpm lint && pnpm test --run && pnpm build
```

## W3-out 잔여 follow-up (트리거 도달 대기)
- 🟠 cross-tenant LRU sub-cap (인증 도입 PR 함께)
- 🟡 multi-worker (Redis pub/sub) — broker registry 재설계
- 🟡 `evict_expired` dirty flag — multi-worker 후
- 🟡 `events_chunks` 별도 테이블 — turn 5000+ events 도달 시
