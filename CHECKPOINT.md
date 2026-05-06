# CHECKPOINT — HiTL Phase 5 Builder v3 Wire 통일

> 마일스톤 게이트 — 사티아 소유. 팀원 완료 보고 시 검증 → done-when 충족 시 done 마킹.
> 브랜치: `feature/hitl-phase5-builder-wire` (main `d387603`에서 분기)
> 참조: `~/.claude/plans/p5-precious-bengio.md`, `docs/design-docs/adr-012-hitl-middleware-migration.md` §Phase 5
> 사용자 결정 (2026-05-06): Router-only 어댑터 + image_choice phase6 JSON.parse fallback + Clean break

---

## 핵심 스코프

ADR-012 마이그레이션의 마지막 옵션 단계. Builder v3 의 ResumeRequest 를 표준 `decisions: Decision[]` 로 통일 + frontend `decisionToBuilderResponse` 어댑터(PR #135) retire.

| 영역 | 결정 |
|------|------|
| `routers/builder.py` `BuilderResumeRequest` | ✅ `decisions` 단일 필드 (clean break) |
| `decisions_to_builder_response` helper | ✅ 신규 (services/builder_service 또는 builder_v3 유틸) |
| `resume_message` handler | ✅ helper 호출 후 graph 전달 |
| `phase6_image.py` (choice/approval wait) | ✅ string JSON.parse fallback (소규모 graph 변경) |
| `_helpers.parse_approval_response` | 🔒 보존 (dict|str 처리 그대로 호환) |
| `builder_v3/graph.py`, `state.py`, 다른 노드 | 🔒 보존 (8-phase 구조) |
| `pending_tool_call_id` stale 검증 | 🔒 보존 |
| frontend `builder-resume-adapter.ts` | ✅ 삭제 (-18) |
| frontend `__tests__/builder-resume-adapter.test.ts` | ✅ 삭제 (-55, 8 가드 retire) |
| frontend `use-chat-runtime.ts:onResumeDecisions` | ✅ 어댑터 호출 제거 + ResumeFn 시그니처 갱신 |
| frontend `stream-builder-resume.ts` | ✅ 시그니처 `Decision[]` + body `{decisions}` |

회귀 위험 최소화: backend helper 가 frontend 어댑터의 책임 그대로 옮겨받음 + phase6 JSON.parse 는 backward compatible.

---

## M0: 거버넌스 초기화 (사티아 DRI)
- [x] 브랜치 `feature/hitl-phase5-builder-wire` 생성 (main `d387603`)
- [x] CHECKPOINT.md 새 사이클로 작성
- [ ] AUDIT.log Phase 5 진입 기록
- 검증: `git branch --show-current`
- done-when: 새 브랜치 + CHECKPOINT + AUDIT 항목
- 상태: in-progress

## M1: 의존성 분석 (베조스 DRI)
- [ ] phase별 wait node 응답 형식 매핑 (어떤 dict shape vs string 기대하는지)
- [ ] 표준 Decision → builder native shape 변환 표 확정
- [ ] phase6 image_choice/approval 에서 JSON-string 회귀 시나리오 정확한 라인 식별
- 검증: `tasks/phase5-dependency-analysis.md` 존재
- done-when: 보고서 + 변환 매핑 + 회귀 가드 후보 명시
- 상태: pending

## M2: ADR + helper 위치 결정 (피차이 DRI, M1 이후)
- [ ] `docs/design-docs/adr-012-hitl-middleware-migration.md` §Phase 5 done-when 명시 + Phase 1~5 완료 회고
- [ ] `decisions_to_builder_response` helper 위치 결정 (services/builder_service vs 신규 builder_v3/_resume_adapter.py)
- 검증: `grep -n "Phase 5 완료" docs/design-docs/adr-012-hitl-middleware-migration.md`
- done-when: ADR §Phase 5 완료 회고 추가, 위치 결정 progress.txt 기록
- 상태: pending

## M3: Backend 구현 (젠슨 DRI, M2 이후)
- [x] `decisions_to_builder_response(decisions)` helper 신규 (services/builder_service.py — approve/reject/respond/edit + 빈 배열 처리)
- [x] `routers/builder.py` `BuilderResumeRequest` schema → `decisions: list[Decision]` (`min_length=1`, `extra='forbid'`)
- [x] `routers/builder.py` `resume_message` handler — helper 호출 후 `run_v3_resume_stream(response=...)` 전달
- [x] `agent_runtime/builder_v3/nodes/_helpers.py` `parse_choice_response` helper 추출 (JSON.parse fallback)
- [x] `agent_runtime/builder_v3/nodes/phase6_image.py` 두 wait 노드 helper 사용으로 단순화 (-9)
- [x] 신규 가드 16건 (`tests/test_builder_phase5.py`):
  - 6 helper unit (TestDecisionsToBuilderResponse: approve/reject±msg/respond/edit/빈배열)
  - 4 router contract (TestResumeRouterContract: standard 200 + approve dict 변환 + legacy 422 + 빈배열 422)
  - 6 phase6 JSON 회귀 (TestPhase6JsonStringFallback: JSON 객체 string + auto_prompt key + invalid JSON + dict/plain 보존 + prompt_keys + 노드 직접 invoke skip 분기)
- 검증: ruff 0 / pyright 0 errors,0 warnings / pytest 865 PASS 회귀 0 / alembic OK
- done-when: ruff 0 / pyright 0/0 / pytest 회귀 0 + 신규 가드 ≥3건 PASS ✅
- 상태: done

## M4: Frontend 구현 (저커버그 DRI, M3 이후)
- [x] `lib/chat/builder-resume-adapter.ts` 삭제
- [x] `lib/chat/__tests__/builder-resume-adapter.test.ts` 삭제 (8 가드 retire)
- [x] `lib/chat/use-chat-runtime.ts:onResumeDecisions` — `decisionToBuilderResponse` 호출 제거, `decisions` 그대로 `resumeFn`에 전달
- [x] `lib/chat/use-chat-runtime.ts` `ResumeFn` 타입 시그니처 갱신 (`response: unknown` → `decisions: Decision[]`)
- [x] `lib/sse/stream-builder-resume.ts` 시그니처 + POST body `{decisions, display_text, interrupt_id}`
- [x] `app/agents/new/conversational/page.tsx` resumeFn 시그니처 갱신 (Decision[] 수용)
- 검증: `cd frontend && pnpm lint && pnpm test --run && pnpm build`
- done-when: lint 0 / vitest 회귀 0 / build PASS / `decisionToBuilderResponse` grep 0건
- 상태: done

## M5: 회귀 검증 + 통합 (베조스 DRI, M3·M4 이후)
- [ ] backend 게이트 4종 + 신규 가드 (M3) 모두 PASS
- [ ] frontend 게이트 3종 + 어댑터 retire 잔재 0
- [ ] grep 검증: `rg "decisionToBuilderResponse" frontend/src` → 0건, `rg "BuilderResumeRequest.*response\b" backend/app` → 0건
- [ ] builder_v3 phase별 회귀 테스트 모두 PASS (test_builder_v3.py 기존 시나리오 보존)
- 검증: 위 명령 모두 통과
- done-when: 전체 통과 + retire 잔재 0
- 상태: pending

## M6: HANDOFF + ADR-012 종료 회고 (사티아 DRI, M5 이후)
- [ ] HANDOFF.md Phase 5 완료 + ADR-012 모든 phase 종료 안내
- [ ] progress.txt 학습 entry
- [ ] AUDIT PROJECT_DONE
- 상태: pending

---

## 보존 영역 (수정 금지)

- `backend/app/agent_runtime/builder_v3/graph.py` (8-phase state machine)
- `backend/app/agent_runtime/builder_v3/state.py`
- `backend/app/agent_runtime/builder_v3/nodes/phase{2,3,4,5,7,8}*.py` (phase6 외)
- `backend/app/agent_runtime/builder_v3/nodes/_helpers.py:parse_approval_response` (dict|str 처리 호환)
- `backend/app/agent_runtime/middleware_registry.py` (Phase 1)
- `backend/tests/test_hitl_middleware.py` (Phase 1 가드)
- `backend/tests/test_hitl_wire.py` 메인 채팅 가드
- `frontend/src/lib/chat/decision-mappers.ts` (PR #136)
- `frontend/src/lib/chat/has-new-assistant-message.test.ts` (PR #134)

## 회귀 위험 최소화 가드

1. `decisions_to_builder_response` helper 가 frontend 어댑터(PR #135)의 매핑을 1:1 이전 — 동작 변경 0
2. phase6 JSON.parse 는 fallback만 추가 — 기존 dict/string 분기 그대로 호환
3. graph + state + 8 phase 노드 변경 0 (phase6 외)
4. 신규 가드 ≥3건 (M3) + builder_v3 기존 시나리오 PASS 검증 (M5)
5. 어댑터 retire 후 grep 0건 검증 (M5)
