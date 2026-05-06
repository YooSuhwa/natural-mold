# 작업 인계 — HiTL Phase 4 완료, Phase 5 진입 검토

> 새 세션 첫 행동: 본 파일 + `docs/design-docs/adr-012-hitl-middleware-migration.md` (Phase 5 §) 참조.

## 마지막 상태

- 브랜치: `feature/hitl-phase4-ask-user-retire` (main `20022dc`에서 분기, **커밋 미수행 — 사용자 승인 대기**)
- backend: **853 PASS** (Phase 3 baseline 847 → -1 의도 삭제 → +7 신규 가드 = 853) / pyright 0/0 / ruff clean / alembic OK
- frontend: **270 PASS** (47 files) / lint clean / build PASS (16 routes)
- retire 잔재 grep: 메인 채팅 0건, builder_v3 + streaming 어댑터만 (의도된 보존)

## Phase 4 (옵션 B retire) 완료

ask_user 도구를 **메인 채팅 한정**으로 retire. builder_v3 native interrupt 패턴 + 어댑터 + UI는 모두 보존(ADR-012 §Phase 5 영역).

**Backend (4 파일)**:
- `tools/ask_user.py` 삭제 (-36)
- `executor.py`: import 제거, `include_ask_user` 파라미터 → `is_trigger_mode`로 rename(트리거 hang 차단 로직만 보존). 도구 conditional append + interrupt_on 자동 등록 제거.
- `streaming.py`: `_interrupt_to_standard_chunk`의 ask_user 어댑터 분기 **코드 보존** + 주석 갱신("builder_v3 native interrupt 어댑터 — 메인 채팅 도구는 retired")
- 테스트:
  - `test_executor.py`: 어설션 5건 갱신, `test_ask_user_not_included_in_invoke` 삭제 (도구 자체 부재로 무의미)
  - `test_hitl_middleware.py:117`: `is_trigger_mode=True`로 갱신
  - `test_hitl_wire.py:280-295, 337-350`: docstring을 "builder_v3 native interrupt 어댑터 검증"으로 명확화
  - **신규**: `test_hitl_phase4_retire.py` 7건 가드

**Frontend (3 파일, +32 lines, 코드 동작 0 변경)**:
- `tool-ui-registry.ts`: BUILDER_TOOL_UI 주석 — Phase 4 retire 컨텍스트 + builder_v3 어댑터 경로 명시
- `use-chat-runtime.ts`: onStandardInterrupt JSDoc + finalize 주석 갱신
- `user-input-ui.tsx`: 컴포넌트 상단 JSDoc 신설 — "Builder v3 전용" + backend 참조

**문서**:
- `docs/design-docs/adr-012-hitl-middleware-migration.md`:
  - 상태 헤더: "Phase 4 진행 중 (옵션 B)"
  - §5: 옵션 A→옵션 B 전환 사유(미들웨어 일원화·트리거 무용·docstring implicit prompt 오염) + builder_v3 보존 영역 명시
  - §Phase 4: Done-when 구체화 + 작업 항목 + 신규 가드 ≥2건
  - §위험: builder_v3 의존성 얽힘 항목 추가, prompt 회귀 항목 갱신
  - §검증: Phase 4 회귀 테스트 항목 갱신 (도구 미주입 가드 + builder_v3 파이프라인)

## 핵심 인사이트 (jensen)

`include_ask_user`는 **두 책임**을 묶고 있었음:
1. ask_user 도구 주입 여부
2. 트리거 모드의 HiTL interrupt 강제 차단

단순 삭제 시 (2) 손실 → 트리거 hang 회귀 위험. **`is_trigger_mode`로 rename**하여 (2)만 의도대로 보존. 외부 호출처 영향 0 (모두 keyword 인자 사용).

## 핵심 인사이트 (bezos)

ask_user는 두 경로에서 사용 — 메인 채팅(retire) vs builder_v3(보존). HANDOFF Phase 3가 안내한 "어댑터 분기 제거"는 옵션 A 가정이었음. **옵션 B 선택 시 builder_v3 의존성으로 어댑터 보존이 필수**. UserInputUI / ko.json userInput 라벨도 동일 이유로 보존.

## 신규 회귀 가드 (`test_hitl_phase4_retire.py` 7건)

1. `test_ask_user_module_is_retired` — 모듈 부재
2. `test_executor_does_not_expose_ask_user_tool_symbol` — 심볼 부재
3. `test_main_chat_stream_never_injects_ask_user` — 메인 채팅 도구 미주입(빈/user_tool 케이스)
4. `test_trigger_invoke_never_injects_ask_user_and_blocks_hitl` — 트리거 invoke + `interrupt_on=None` 강제
5. `test_builder_v3_native_ask_user_with_options_adapted_to_respond_chunk`
6. `test_builder_v3_native_ask_user_without_options_adapted_to_respond_chunk`
7. `test_unknown_native_interrupt_shape_is_skipped_after_retire`

## 보존 영역 (Phase 5에서도 유지)

- `backend/app/agent_runtime/streaming.py`의 ask_user 어댑터 분기 (builder_v3 의존성)
- `backend/app/agent_runtime/builder_v3/**` (자체 native interrupt 패턴)
- `backend/app/routers/builder.py` (BuilderResumeRequest)
- `frontend/src/components/chat/tool-ui/user-input-ui.tsx` (builder_v3 UI)
- `frontend/messages/ko.json` userInput 라벨군
- `backend/tests/test_builder_v3.py` (Phase 5 영역)

## 검증 명령

```
cd backend && uv run alembic upgrade head && uv run ruff check . && uv run pytest tests/ && uv run pyright app/ tests/
cd frontend && pnpm lint && pnpm test --run && pnpm build
```

## 커밋 시 주의

git status에 **스코프 외 자동 갱신 파일**이 함께 보임:
- `backend/app/data/model_catalog/catalog.json`
- `backend/app/data/model_catalog/fetch_metadata.json`
- `backend/app/data/model_catalog/sources/{ai_model_list,openrouter_models,pydantic_genai_prices}.json`

이는 카탈로그 cron(6시간) 자동 갱신. **이번 PR 스코프 외이므로 staging에서 제외** 또는 별도 PR로 처리.

## Phase 5 (옵션, ~150 라인) — Builder v3 wire format 통일

**Done-when**: Builder v3 의 ResumeRequest 도 표준 `decisions` 형식 받음 (graph 자체는 변경 X)

작업 (ADR-012 §Phase 5):
- `routers/builder.py`의 `BuilderResumeRequest` dual-shape → 표준만
- frontend builder UI 동일 어댑터 사용
- builder graph 자체는 native interrupt 패턴 유지

## W3-out 잔여 follow-up (트리거 도달 대기)
- 🟠 cross-tenant LRU sub-cap (인증 도입 PR 함께)
- 🟡 multi-worker (Redis pub/sub) — broker registry 재설계
- 🟡 `evict_expired` dirty flag — multi-worker 후
- 🟡 `events_chunks` 별도 테이블 — turn 5000+ events 도달 시
