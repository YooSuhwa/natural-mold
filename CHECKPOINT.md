# CHECKPOINT — HiTL Phase 4 ask_user Retire (Option B)

> 마일스톤 게이트 — 사티아 소유. 팀원 완료 보고 시 `검증` 실행 → done-when 충족 시 done 마킹.
> 브랜치: `feature/hitl-phase4-ask-user-retire` (main `20022dc`에서 분기)
> 참조: `HANDOFF.md` (Phase 3 인계), `docs/design-docs/adr-012-hitl-middleware-migration.md` §Phase 4
> 사용자 결정 (2026-05-06): 옵션 B (retire) + 풀 5인 사일로 + 회귀 위험 최소화 꼼꼼히

---

## 핵심 스코프 정정 (베조스 의존성 분석 결과)

ask_user 도구는 **두 경로**에서 사용:

1. **메인 채팅 (executor.py)** — `ask_user_tool`이 `include_ask_user=True`일 때 자동 주입. 도구로서 LLM에 노출.
2. **Builder v3 (보존 영역)** — `phase2_intent.py` + `router.py`가 `interrupt({"type":"ask_user", ...})` 직접 발행. 도구 X, native interrupt만.

**Phase 4 retire 범위 = (1)만**. (2)는 ADR-012 §Phase 5 보존 영역(자체 native interrupt 패턴 유지). 따라서:

| 영역 | 결정 |
|------|------|
| `tools/ask_user.py` | ✅ 삭제 |
| `executor.py`의 ask_user import + 등록 + interrupt_on 자동 등록 + `include_ask_user` 파라미터 | ✅ 제거 |
| `streaming.py`의 ask_user 어댑터 분기 | 🔒 **보존** (builder_v3 native interrupt 처리용) — 주석 갱신 |
| `builder_v3/**` 코드 | 🔒 보존 (ADR-012 Phase 5) |
| `frontend/src/components/chat/tool-ui/user-input-ui.tsx` | 🔒 보존 (builder_v3 의존) |
| `tool-ui-registry.ts:34, 51, 57` ask_user 참조 | 🔒 보존 (builder_v3) — 주석 갱신 |
| `messages/ko.json:558-571` userInput 라벨 | 🔒 보존 (builder_v3) |
| `routers/builder.py:39` ask_user docstring | 🔒 보존 (builder_v3 영역) |
| 테스트 (test_executor.py, test_hitl_middleware.py, test_hitl_wire.py) | ✅ 갱신 (메인 채팅 ask_user 어설션 제거) |
| `test_builder_v3.py:177-230` | 🔒 보존 |

회귀 위험 최소화 핵심: **streaming.py 어댑터 보존** + **frontend UI 보존** = 시스템 차원 호환성 유지.

---

## M0: 거버넌스 초기화 (사티아 DRI)
- [x] 브랜치 `feature/hitl-phase4-ask-user-retire` 생성 (main `20022dc`)
- [x] CHECKPOINT.md 새 사이클로 재작성
- [x] AUDIT.log Phase 4 진입 기록
- [x] `tasks/phase4-dependency-analysis.md` — 베조스 분석 보고서 저장
- 검증: `git branch --show-current && test -f tasks/phase4-dependency-analysis.md`
- done-when: 새 브랜치 + CHECKPOINT 갱신 + AUDIT 항목 + 분석 보고서 존재
- 상태: done

## M1: 의존성 매핑 (베조스 DRI)
- [x] ask_user 심볼 전체 매핑 (코드/테스트/시드/번역/문서)
- [x] retire vs 보존 영역 분리 (메인 채팅 vs builder_v3)
- [x] 회귀 리스크 맵
- 검증: `test -f tasks/phase4-dependency-analysis.md`
- done-when: 모든 출현 위치 file:line 매핑 완료, 보존/제거 결정 명확
- 상태: done

## M2: 아키텍처 결정 + ADR 갱신 (피차이 DRI)
- [ ] `docs/design-docs/adr-012-hitl-middleware-migration.md` §Phase 4 갱신 — "옵션 B 선택" + "메인 채팅만 retire, builder_v3 보존" 명기
- [ ] §5 옵션 B 결정 사유 + 회귀 가드 전략 추가
- [ ] §위험 표에 builder_v3 의존성 항목 추가
- 검증: `grep -n "옵션 B\|Option B" docs/design-docs/adr-012-hitl-middleware-migration.md | head -3`
- done-when: ADR §Phase 4 + §5 + §위험 갱신 완료, 보존 영역 명시
- 상태: done — 상태 헤더 + §5 + §Phase 4 + §위험 + §검증 갱신, system_prompt 영향 분석(직접 언급 0, docstring implicit 오염) 반영

## M3: Backend retire (젠슨 DRI, M2 이후)
- [ ] `backend/app/agent_runtime/tools/ask_user.py` 삭제
- [ ] `backend/app/agent_runtime/executor.py:34` import 제거
- [ ] `executor.py:439-443` `include_ask_user` 파라미터 제거 (모든 호출처)
- [ ] `executor.py:502-509` interrupt_on 자동 등록 + 트리거 차단 코드 제거
- [ ] `executor.py:557-558` ask_user 도구 conditional append 제거
- [ ] `streaming.py:84-117` ask_user 어댑터 분기 **주석 갱신만** (코드 보존, "builder_v3 native interrupt 전용" 명시)
- [ ] tests:
  - [ ] `test_executor.py:125-129, 185, 235, 272, 344, 551-575` 어설션 갱신 (ask_user 자동 주입 X)
  - [ ] `test_hitl_middleware.py:117` `include_ask_user=False` 호출 제거
  - [ ] `test_hitl_wire.py:280-295, 337-350` 시나리오는 builder_v3 발행 케이스로 명확화 (테스트명/주석 갱신)
- 검증: `cd backend && uv run alembic upgrade head && uv run ruff check . && uv run pytest tests/ && uv run pyright app/ tests/`
- done-when: ruff 0 / pyright 0/0 / pytest 회귀 0 / ask_user 도구 grep 0건 (메인 채팅 영역)
- 상태: done — `include_ask_user` → `is_trigger_mode` rename(트리거 hang 차단 로직 보존), 6 파일 변경, ruff/pyright clean, pytest 846 PASS

## M4: Frontend 검토 + 주석 갱신 (저커버그 DRI, M3와 병렬 가능)
- [ ] `frontend/src/lib/chat/tool-ui-registry.ts:54` 주석 갱신 — "Builder v3 전용 (메인 채팅 ask_user 도구는 retired)"
- [ ] `frontend/src/lib/chat/use-chat-runtime.ts:100, 430-432` 주석 갱신 — "builder_v3 native interrupt 어댑터 경유"
- [ ] `frontend/src/components/chat/tool-ui/user-input-ui.tsx` 코드 변경 X — 주석/JSDoc만 "Builder v3 전용" 명시
- [ ] `frontend/messages/ko.json:558-571` userInput 라벨 보존 (builder_v3 사용)
- 검증: `cd frontend && pnpm lint && pnpm test --run && pnpm build`
- done-when: lint 0 / test 회귀 0 / build 성공 / 주석 갱신 PR diff 명확
- 상태: done — 3 파일 +32 라인 주석/JSDoc만 갱신, 코드 동작 변경 0, lint 0 / vitest 270 / build PASS

## M5: 회귀 검증 + Phase 4 가드 테스트 (베조스 DRI, M3·M4 이후)
- [x] backend retire 회귀: 메인 채팅 ask_user 도구 grep 0건 (builder_v3 제외)
- [x] **신규 가드 테스트**: 메인 채팅에서 LLM이 ask_user 호출하지 않음 검증 (도구 미주입이므로 호출 불가)
- [x] **신규 가드 테스트**: builder_v3 native interrupt → streaming 어댑터 → 표준 respond chunk 전체 파이프라인 회귀 0
- [x] 통합: backend 게이트 4종 + frontend 게이트 3종 모두 통과
- 검증: `cd backend && uv run pytest tests/test_hitl_phase4_retire.py -v` → 7/7 PASS
- done-when: 회귀 0 + 신규 가드 ≥2건 PASS + 게이트 7/7 통과
- 상태: done — 신규 파일 `backend/tests/test_hitl_phase4_retire.py` 7건 가드, backend 853 PASS / frontend 270 PASS, 회귀 0

## M6: 통합 검증 + HANDOFF (사티아 DRI, M5 이후)
- [ ] backend: alembic + ruff + pytest + pyright 모두 통과
- [ ] frontend: lint + test + build 모두 통과
- [ ] grep retire 검증: `rg -n "ask_user_tool|include_ask_user" backend/app` (test 제외 0건)
- [ ] HANDOFF.md Phase 4 완료 + Phase 5 진입 정보 갱신 (또는 ADR-012 마이그레이션 종료)
- 검증: 위 명령 모두 통과
- done-when: 전체 통과 + retire 잔재 0 + HANDOFF 갱신
- 상태: done — backend 게이트 4/4 + frontend 게이트 3/3 PASS, 메인 채팅 retire 잔재 0건, HANDOFF Phase 5 진입 정보 갱신, 사용자 승인 대기

---

## 수정 금지 (보존 영역)
- `backend/app/agent_runtime/middleware_registry.py` (Phase 1)
- `backend/app/agent_runtime/builder_v3/**` (Phase 5까지, 자체 native interrupt)
- `backend/tests/test_hitl_middleware.py` (Phase 1, 5건 회귀 가드 — 단 L117만 갱신)
- `backend/tests/test_builder_v3.py` (Phase 5까지)
- `backend/app/routers/builder.py` (Phase 5까지)

## 회귀 위험 최소화 가드
1. streaming.py 어댑터 보존 → builder_v3 호환
2. UserInputUI 컴포넌트 보존 → builder_v3 UI 호환
3. ko.json userInput 라벨 보존 → 빌더 UI 라벨 유지
4. 신규 가드 테스트 ≥2건 (M5)
5. 베조스 회귀 검증 + 사티아 통합 검증 2단계 게이트
