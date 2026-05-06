# 작업 인계 — HiTL Phase 4 옵션 A 최종 결정 + 카탈로그 fix

> 새 세션 첫 행동: 본 파일 + `docs/design-docs/adr-012-hitl-middleware-migration.md` 참조.

## 마지막 상태

- 브랜치: `feature/hitl-phase4-ask-user-retire` (main `20022dc`에서 분기)
- 커밋: 3개 (e7140fd 카탈로그 fix + 8f81439 Phase 4 retire + 221c4dd Phase 4 revert)
- backend: ruff/pyright clean / pytest 재검증 필요 / alembic OK
- frontend: lint clean / vitest 270 PASS / build PASS

## 이번 PR에 남는 변경 사항

### ✅ 유지 — HiTL 미들웨어 카탈로그 노출 fix (`e7140fd`)

ADR-012 Phase 1과 어긋나 사용자가 frontend "미들웨어 추가" UI에서 HiTL을 선택할 수 없던 버그 수정.

- `middleware_registry.py`: `DEEPAGENT_AUTO_INJECTED_TYPES` + `EXPLICITLY_INSTANTIATED_TYPES` 두 set 분리. `DEEPAGENT_BUILTIN_TYPES`는 union으로 유지 (build 단계 중복 방지용).
- `get_middleware_registry(exclude_builtin=True)`는 `AUTO_INJECTED`만 제외 → `human_in_the_loop` 노출.
- `executor.py:filtered_mw`는 union 그대로 사용 (변경 없음).
- 신규 가드 3건: `test_middleware_registry` 2건 + `test_list_middlewares` 어설션 보강.

### ❌ Revert — Phase 4 옵션 B retire (`8f81439` → `221c4dd`)

사용자 수동 검증에서 "되물어보기" UX 손실 발견. ADR-012 §Phase 4 옵션 A 최종 결정으로 회귀.

- `tools/ask_user.py` 복원
- `executor.py`의 `include_ask_user` 파라미터 + 자동 등록 + interrupt_on 자동 추가 복원
- `streaming.py` 어댑터 분기 주석 원상 복귀
- frontend 주석/JSDoc 원상 복귀
- 신규 가드 7건 + 의존성 분석 보고서 함께 revert

## Phase 4 결정 회고 (progress.txt + ADR-012 회고 §)

핵심 인사이트:
- HumanInTheLoopMiddleware = 위험 도구 실행 게이트
- ask_user = 사용자 자연어 질문 도구
- 두 책임 직교 — 통합 시도(옵션 B)는 UX 손실

학습 포인트:
1. 도구 retire 결정 시 "코드 단순화" 측면만 보지 말고 "UX 시나리오"를 명시적으로 매핑.
2. `include_ask_user` 두 책임 분리 (도구 주입 vs 트리거 hang 차단)는 가치 있는 발견 — 별도 PR로 다시 진행 가능.
3. 자동 테스트만으로는 UX 회귀 못 잡음 — 수동 E2E (특히 builder_v3 + 메인 채팅 cross 시나리오) 필수.
4. 사용자 검증 단계에서 발견된 회귀를 즉시 인정하고 revert가 sunk cost 추구보다 옳다.

## ADR-012 마이그레이션 현재 상태

| Phase | 상태 |
|-------|------|
| Phase 0 분석 + ADR | ✅ 완료 (main) |
| Phase 1 표준 미들웨어 인프라 | ✅ 완료 (main) |
| Phase 2 Wire format dual-path | ✅ 완료 (main) |
| Phase 3 Legacy wire 제거 | ✅ 완료 (main) |
| **Phase 4 ask_user 검토** | ✅ **옵션 A 최종 결정** (보존) — 본 PR |
| Phase 5 Builder v3 wire 통일 (옵션) | ⏳ 미진입 |

## 별도 PR 후보 (이번 PR 스코프 외)

1. **`include_ask_user` 두 책임 분리** — Phase 4 시도에서 발견. 도구 주입은 그대로, "트리거 hang 차단"만 별도 indicator(예: `is_trigger_mode`)로 정정. ask_user retire와 무관하게 별도 진행 가능.
2. **답변 두 번 표시 회귀** — DB 1건이지만 frontend가 streamingMessages를 안 비우는 회귀. 새로고침 시 정상화. 원인 추정: `runId` (`X-Run-Id`) ↔ DB assistant message id 불일치 가능성. Phase 4 revert와 무관할 수 있어 별도 추적.
3. **빈 fallback chunk UX**: backend가 `aget_state` 실패 시 emit하는 `{action_requests:[],review_configs:[]}`은 frontend chat 페이지에서 미노출. 명시 fallback UI 핸들러 필요 시 chat 페이지에 `onStandardInterrupt` 주입.
4. **builder 어댑터 추출**: `use-chat-runtime.ts` 내부 흡수된 builder 호환 코드 → `lib/chat/builder-resume-adapter.ts` 분리.
5. **Decision 매퍼 통합**: 인라인 객체 리터럴 11곳 → `lib/chat/decision-mappers.ts` (toApprove/toReject/toEdit/toRespond).

## 검증 명령

```
cd backend && uv run alembic upgrade head && uv run ruff check . && uv run pytest tests/ && uv run pyright app/ tests/
cd frontend && pnpm lint && pnpm test --run && pnpm build
```

## 커밋 시 주의

git status에 **스코프 외 자동 갱신 파일** (catalog cron 결과물) 포함 가능:
- `backend/app/data/model_catalog/catalog.json`
- `backend/app/data/model_catalog/fetch_metadata.json`
- `backend/app/data/model_catalog/sources/{ai_model_list,openrouter_models,pydantic_genai_prices}.json`

→ staging에서 제외 또는 별도 PR.

## W3-out 잔여 follow-up (트리거 도달 대기)

- 🟠 cross-tenant LRU sub-cap (인증 도입 PR 함께)
- 🟡 multi-worker (Redis pub/sub) — broker registry 재설계
- 🟡 `evict_expired` dirty flag — multi-worker 후
- 🟡 `events_chunks` 별도 테이블 — turn 5000+ events 도달 시
