# CHECKPOINT — HiTL Phase 3 Transition End

> 마일스톤 게이트 — 사티아 소유. 팀원 완료 보고 시 `검증` 실행 → done-when 충족 시 done 마킹.
> 브랜치: `feature/hitl-phase3-transition-end` (main `be5a735`에서 분기)
> 참조: `HANDOFF.md` (Phase 2 인계), `docs/design-docs/adr-012-hitl-middleware-migration.md` §Phase 3, `docs/exec-plans/active/hitl-phase2-contract.md` §6 transition off.
> Phase 2 CHECKPOINT는 git history에 보존됨 (Plans are Disposable).

---

## M0: 브랜치 + 거버넌스 초기화 (사티아 DRI)
- [x] 브랜치 `feature/hitl-phase3-transition-end` 생성
- [x] CHECKPOINT.md 새 사이클로 재작성
- [x] AUDIT.log Phase 3 진입 기록
- 검증: `git branch --show-current`
- done-when: 새 브랜치 + CHECKPOINT 갱신 + AUDIT 항목 추가
- 상태: done

## M1: 삭제 분석 (베조스 DRI, M0 이후)
- [ ] `tasks/deletion-analysis-phase3.md` — legacy 잔재 정확한 grep 매핑 (파일/라인/심볼)
- [ ] 보존 영역 vs 제거 영역 분리 명세
- [ ] 테스트 파일별 legacy 시나리오 식별 (rename/삭제 결정)
- 검증: `test -f tasks/deletion-analysis-phase3.md`
- done-when: 보고서 존재 + grep 잔재 0 목표 라인 매핑 완료
- 상태: done

## M2: Backend Legacy 제거 (젠슨 DRI, M1 이후 — M3와 병렬)
- [ ] `backend/app/schemas/conversation.py` — `ResumeRequest.response` 필드 + dual-shape model_validator 제거 → `decisions` 단일 필드
- [ ] `backend/app/routers/conversations.py:resume_message` — `response` → `respond` decision 변환 분기 삭제
- [ ] `backend/app/agent_runtime/streaming.py` — 정상 분기 legacy chunk(`{interrupt_id, value}`) emit 삭제 → 표준 `{action_requests, review_configs}` 단독
- [ ] `backend/tests/test_hitl_phase2_wire.py` 정리 — legacy 시나리오 삭제, 표준 보존, `test_hitl_wire.py`로 rename
- 검증: `cd backend && uv run alembic upgrade head && uv run ruff check . && uv run pytest tests/ && uv run pyright app/ tests/`
- done-when: ruff 0 / pyright 0/0 / pytest 회귀 0
- 상태: done

## M3: Frontend Legacy 제거 (저커버그 DRI, M1 이후 — M2와 병렬)
- [ ] `frontend/src/lib/types/index.ts` — `InterruptPayload` union → 표준 단일 (`{action_requests, review_configs}`)
- [ ] `frontend/src/lib/sse/stream-resume.ts` — legacy `streamResume` 삭제, `streamResumeDecisions`만 유지
- [ ] `frontend/src/lib/chat/hitl-context.ts` — `onResume` 어댑터 삭제, `onResumeDecisions`만 유지
- [ ] `frontend/src/lib/chat/use-chat-runtime.ts` — legacy 분기/dedup 제거, 단일 경로로 평탄화
- [ ] `frontend/messages/ko.json` — 사용 중지된 legacy 라벨 정리
- [ ] frontend 테스트(`stream-resume.test.ts`, `use-chat-runtime-hitl.test.tsx`) — legacy 시나리오 삭제
- 검증: `cd frontend && pnpm lint && pnpm test --run && pnpm build`
- done-when: lint 0 / test 회귀 0 / build 성공
- 상태: done

## M4: 통합 검증 + 잔재 0 (베조스 DRI, M2·M3 이후)
- [ ] backend: alembic + ruff + pytest + pyright 모두 통과
- [ ] frontend: lint + test + build 모두 통과
- [ ] grep 잔재 0: `rg -n "ResumeRequest.*response[^_]|streamResume\b|onResume\b" backend/app frontend/src`
- 검증: 위 명령 모두 0 exit
- done-when: 전체 통과 + legacy 잔재 0
- 상태: done

## M5: contract 이동 + HANDOFF (사티아 DRI, M4 이후)
- [x] `docs/exec-plans/active/hitl-phase2-contract.md` → `docs/exec-plans/completed/hitl-phase2-contract.md`
- [x] index.md 파일 없음 — 스킵
- [x] `HANDOFF.md` Phase 4 진입 정보로 갱신
- 검증: `test -f docs/exec-plans/completed/hitl-phase2-contract.md && ! test -f docs/exec-plans/active/hitl-phase2-contract.md`
- done-when: contract 이동 완료 + HANDOFF Phase 4 안내 반영
- 상태: done

---

## 수정 금지 (다른 Phase 영역 — Phase 3 범위 외)
- `backend/app/agent_runtime/middleware_registry.py` (Phase 1)
- `backend/app/agent_runtime/executor.py:_prepare_agent` (Phase 1, 트리거 차단)
- `backend/tests/test_hitl_middleware.py` (Phase 1, 5건 회귀 가드)
- `backend/app/agent_runtime/tools/ask_user.py` (Phase 4까지)
- `backend/app/agent_runtime/builder_v3/**` (Phase 5까지, 자체 native interrupt)
