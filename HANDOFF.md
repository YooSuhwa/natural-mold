# 작업 인계 — HiTL Phase 4 옵션 A 최종 + 카탈로그 fix

> 새 세션 첫 행동: 본 파일 + `docs/design-docs/adr-012-hitl-middleware-migration.md` (Phase 4 회고 §) 참조.

## 마지막 상태

- 브랜치: `feature/hitl-phase4-ask-user-retire` (main `20022dc` 분기)
- 게이트 7/7 PASS — backend 849 / pyright 0/0 / ruff clean / alembic OK + frontend 270 / lint clean / build PASS
- 미커밋: `middleware_registry.py` docstring 갱신(simplify 결과). 다음 커밋 또는 amend 후보.
- 사용자 수동 검증 완료: ask_user 카드 정상 표시, 미들웨어 카탈로그에 HiTL 노출 확인

## 커밋 히스토리

| Hash | 의미 |
|------|------|
| `8f81439` | Phase 4 옵션 B retire (시도) |
| `e7140fd` | HiTL 카탈로그 노출 fix (유지) |
| `221c4dd` | Phase 4 retire revert |
| `ff8ab58` | ADR-012 옵션 A 최종 회고 + HANDOFF |

## PR 최종 효과 (main 대비 남는 변경)

✅ HiTL 미들웨어가 `/api/middlewares` 카탈로그에 노출 → 사용자가 UI에서 추가 가능
✅ `DEEPAGENT_AUTO_INJECTED_TYPES` + `EXPLICITLY_INSTANTIATED_TYPES` 두 set 분리 (build vs catalog 정책 분리)
✅ 회귀 가드 3건 (`test_middleware_registry` 2 + `test_list_middlewares` 어설션)
✅ ADR-012 §Phase 4 옵션 A 최종 결정 회고 §추가

## Phase 4 학습 (progress.txt + ADR 회고 §)

`HumanInTheLoopMiddleware` = 위험 도구 게이트 / `ask_user` = 자연어 질문 도구 — **두 책임 직교**. 옵션 B가 단순화는 했지만 "되물어보기" UX를 잃어 사용자 검증에서 회귀 발견 → 즉시 revert. 향후 옵션 B 재시도 금지.

## 남은 작업 (별도 PR 후보)

### 우선순위 높음
1. **답변 두 번 표시 회귀** — DB 1건이지만 frontend가 streamingMessages 미클리어. 새로고침 시 정상화. `runId`(`X-Run-Id`) ↔ DB assistant message id 불일치 추정. HiTL 미들웨어 추가가 트리거일 수 있음. 위치: `frontend/src/lib/chat/use-chat-runtime.ts:457-473`

### 우선순위 중간
2. **`include_ask_user` 두 책임 분리 재시도** — Phase 4에서 발견. 도구 주입 책임은 그대로, 트리거 hang 차단만 별도 indicator(`is_trigger_mode`)로 분리. retire와 무관하게 진행 가능
3. **빈 fallback chunk UX** — backend `aget_state` 실패 시 emit하는 `{action_requests:[],review_configs:[]}` chat 페이지 미노출. 명시 fallback 핸들러 필요 시 `onStandardInterrupt` 주입

### 우선순위 낮음 (코드 품질)
4. builder 어댑터 추출 — `use-chat-runtime.ts` 내부 흡수된 builder 호환 코드를 `lib/chat/builder-resume-adapter.ts` 분리
5. Decision 매퍼 통합 — 인라인 객체 리터럴 11곳 → `lib/chat/decision-mappers.ts`

### ADR-012 마이그레이션 마지막 단계
6. **Phase 5 (옵션, ~150 라인)** — Builder v3 `BuilderResumeRequest` 표준 `decisions` 형식. graph 자체는 native interrupt 유지

### W3-out 잔여
- 🟠 cross-tenant LRU sub-cap (인증 도입 PR 함께)
- 🟡 multi-worker (Redis pub/sub) — broker registry 재설계
- 🟡 `evict_expired` dirty flag (multi-worker 후)
- 🟡 `events_chunks` 별도 테이블 (turn 5000+ events 도달 시)

## 보존 영역 (수정 금지)

- `backend/app/agent_runtime/middleware_registry.py:DEEPAGENT_AUTO_INJECTED_TYPES` (deepagents 자동 주입 — 카탈로그 노출 시 AssertionError)
- `backend/app/agent_runtime/builder_v3/**` (Phase 5까지)
- `backend/app/agent_runtime/tools/ask_user.py` (옵션 A 최종)
- `backend/tests/test_hitl_middleware.py` (Phase 1 회귀 가드)

## 검증 명령

```
cd backend && uv run alembic upgrade head && uv run ruff check . && uv run pytest tests/ && uv run pyright app/ tests/
cd frontend && pnpm lint && pnpm test --run && pnpm build
```

## 커밋 시 주의

스코프 외 자동 갱신 파일 (catalog cron 6시간 결과물) staging 제외:
- `backend/app/data/model_catalog/{catalog,fetch_metadata}.json`
- `backend/app/data/model_catalog/sources/{ai_model_list,openrouter_models,pydantic_genai_prices}.json`
