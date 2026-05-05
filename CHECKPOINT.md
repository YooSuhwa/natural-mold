# CHECKPOINT — HiTL Phase 2 Wire Format 통합

> 마일스톤 게이트 — 사티아가 소유. 팀원 완료 보고 시 `검증` 실행 → done-when 충족 시 done 마킹.
> 브랜치: `feature/hitl-phase2-wire-format` (main `750d587`에서 분기)
> 참조: `HANDOFF.md` (Phase 1 산출물), `docs/design-docs/adr-012-hitl-middleware-migration.md` §Phase 2.
> Phase 1 CHECKPOINT는 git history에 보존됨 (Plans are Disposable — 새 사이클에 새 체크포인트).

---

## M0: Wire Contract 정의 (피차이 DRI)
- [x] `docs/exec-plans/active/hitl-phase2-contract.md` — backend/frontend 양쪽이 참조할 dual-shape 계약 (ResumeRequest, INTERRUPT event payload, transition 윈도우 규칙)
- 검증: `test -f docs/exec-plans/active/hitl-phase2-contract.md`
- done-when: 계약 문서 존재 + Decision 스키마 / action_requests / review_configs / transition emit 규칙 모두 명시
- 상태: done

## M1: Backend Dual-Shape (젠슨 DRI, M0 이후)
- [x] `backend/app/schemas/conversation.py` — `ResumeRequest{decisions?, response?}` dual-shape (둘 중 하나 이상 필수, model_validator 검증)
- [x] `backend/app/schemas/conversation.py` — 신규 `Decision` 모델 (`type: Literal["approve","edit","reject","respond"]` + payload)
- [x] `backend/app/routers/conversations.py:resume_message` — `decisions` → `Command(resume={"decisions":[...]})`. `response`만 들어오면 단일 respond decision으로 변환 후 동일 dict 송신.
- [x] `backend/app/agent_runtime/streaming.py:331-367` — `GraphInterrupt` catch 시 표준 `{action_requests, review_configs}` chunk + 기존 `{interrupt_id, value}` chunk dual emit (transition window).
- [x] `backend/app/agent_runtime/executor.py` — `resume_agent_stream` 시그니처 보존(`resume_value: Any`); router가 dict payload `{"decisions": [...]}` 송신.
- 검증: `cd backend && uv run ruff check . && uv run pyright app/ && uv run pytest tests/`
- done-when: ruff 0 / pyright 0 / pytest 회귀 0 (기존 826 + 신규 PASS)
- 상태: done

## M2: Frontend Dual-Shape (저커버그 DRI, M0 이후 — M1과 병렬)
- [x] `frontend/src/lib/types/index.ts` — `InterruptPayload` 표준 + 기존 union (action_requests / review_configs / interrupt_id+value)
- [x] `frontend/src/lib/types/index.ts` — `Decision` 타입 + `ResumeDecisionsRequest` 신규
- [x] `frontend/src/lib/chat/use-chat-runtime.ts` — `case 'interrupt'` 표준 payload 처리 (handledStandardInterruptIdsRef dedup + onStandardInterrupt 콜백), legacy payload는 기존 onInterrupt 경로 보존
- [x] `frontend/src/lib/sse/stream-resume.ts` — `streamResumeDecisions` 신규 (`{decisions}` 송신), 기존 `streamResume` 시그니처 보존
- [x] `frontend/src/lib/chat/hitl-context.ts` — `HiTLContextValue`에 `onResumeDecisions(decisions[])` 추가, `onResume`은 어댑터로 보존
- [x] `frontend/messages/ko.json` — `chat.approval.respond`, `chat.approval.allActionsCompleted`, `confirmAll`, `actionN`, `respondPlaceholder` 라벨 추가
- 검증: `cd frontend && pnpm lint && pnpm test --run && pnpm build`
- done-when: lint 0 / test 회귀 0 / build 성공
- 상태: done

## M3: 회귀 가드 (베조스 DRI, M1·M2 이후)
- [x] `backend/tests/test_hitl_phase2_wire.py` (신규, 23건) — Decision/ResumeRequest 스키마(13) + router 표준/legacy/공존/422(6) + streaming dual emit + ask_user-only legacy + fallback(3). contract 결정사항 1:1 매칭.
- [x] `frontend/src/lib/sse/__tests__/stream-resume.test.ts` (신규, 6건) + `frontend/src/lib/chat/__tests__/use-chat-runtime-hitl.test.tsx` (신규, 9건 — 8 PASS + 1 expected-fail). 표준/legacy 분기 + dedup + multi-action + onResumeDecisions noop + body shape 검증.
- 검증: `cd backend && uv run pytest tests/test_hitl_phase2_wire.py -v && cd ../frontend && pnpm test --run`
- done-when: 신규 테스트 전부 PASS, 기존 회귀 0
- 상태: done — backend 849 PASS (826+23, 회귀 0) / frontend 276 PASS + 1 expected fail (262+14, 회귀 0). 발견 이슈 1건 (fallback 경로 onInterrupt 2회 호출 — §4.5 위반, rare path) → SendMessage("?") 저커버그 보고됨.

## M4: 통합 검증 + HANDOFF (사티아 DRI, M1·M2·M3 이후)
- [x] backend: alembic upgrade head OK + ruff clean + pytest **849 PASS** (회귀 0) + pyright **0/0**
- [x] frontend: pnpm lint clean + vitest **276 PASS** (47 files) + pnpm build PASS (16 routes)
- [x] HANDOFF.md Phase 3 진입 안내 갱신 (dual-path 제거 작업 명세)
- 검증: 위 명령 모두 0 exit ✅
- done-when: 전체 명령 통과 + HANDOFF Phase 3 안내 반영 ✅
- 상태: **done**

---

## 수정 금지 (Phase 1 산출물 / 다른 Phase 영역)
- `backend/app/agent_runtime/middleware_registry.py` — Phase 1 주석 보존
- `backend/app/agent_runtime/executor.py:_prepare_agent` 시그니처 + `include_ask_user=False` 트리거 차단 동작
- `backend/tests/test_hitl_middleware.py` — Phase 1 5건 회귀 가드 보존
- `backend/app/agent_runtime/tools/ask_user.py` — Phase 4까지 보존
- `backend/app/agent_runtime/builder_v3/**` — Phase 5까지 보존 (자체 native interrupt 패턴)
