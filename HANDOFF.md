# 작업 인계 — HiTL Phase 5 완료, ADR-012 마이그레이션 종료

> 새 세션 첫 행동: 본 파일 + `docs/design-docs/adr-012-hitl-middleware-migration.md` (Phase 1~5 완료 회고) 참조.

## 마지막 상태

- 브랜치: `feature/hitl-phase5-builder-wire` (main `d387603`에서 분기, **커밋 미수행 — 사용자 승인 대기**)
- backend: **865 PASS** (Phase 4 baseline 849 + 16 Phase 5 가드) / pyright 0/0 / ruff clean / alembic OK
- frontend: **284 PASS** (49 files, Phase 4 baseline 292 - 8 retire) / lint clean / build PASS
- 어댑터 retire 잔재 grep: `decisionToBuilderResponse` 0건, `payload.response` 0건

## Phase 5 완료 사항

ADR-012 §Phase 5 — Builder v3 wire format 통일. 사용자 결정: Router-only 어댑터 + Clean break + image_choice JSON.parse fallback.

**Backend (5 파일, +389/-26)**:
- `services/builder_service.py`: `decisions_to_builder_response(decisions)` helper 신규 (+36)
- `routers/builder.py`: `BuilderResumeRequest{decisions: list[Decision]}` schema clean break + handler 갱신 (`min_length=1`, `extra='forbid'`)
- `agent_runtime/builder_v3/nodes/_helpers.py`: `parse_choice_response` helper 신규 (+41) — JSON.parse fallback 두 wait 노드 공유
- `agent_runtime/builder_v3/nodes/phase6_image.py`: 두 wait 노드 helper 사용으로 단순화 (-9)
- `tests/test_builder_phase5.py`: 신규 가드 16건 (helper 6 + router contract 4 + phase6 JSON 6)

**Frontend (5 파일, +15/-91)**:
- `lib/chat/builder-resume-adapter.ts` 삭제 (-18)
- `lib/chat/__tests__/builder-resume-adapter.test.ts` 삭제 (-55, 8 가드 retire)
- `lib/chat/use-chat-runtime.ts`: import + `ResumeFn` 시그니처 갱신, `onResumeDecisions`에서 어댑터 호출 제거
- `lib/sse/stream-builder-resume.ts`: 시그니처 `Decision[]` + POST body `{decisions, ...}`
- `app/agents/new/conversational/page.tsx`: resumeFn 시그니처 갱신

**Docs**:
- `docs/design-docs/adr-012-hitl-middleware-migration.md`: §Phase 5 완료 회고 + §위험 갱신 + 상태 헤더 "Phase 1~5 완료"
- `tasks/phase5-dependency-analysis.md`: 베조스 분석 보고서
- `progress.txt` + `AUDIT.log`: 학습 entry

## 핵심 인사이트

1. **Router-only 어댑터**: builder graph + state + 8 phase 노드 변경 0 보존. `decisions_to_builder_response`가 frontend 어댑터(PR #135) 책임을 1:1 이전.
2. **`parse_choice_response` 추출**: phase6 두 wait 노드(choice/approval)가 동일 정규화 로직 공유. JSON.parse는 `{`+`}` 휴리스틱 가드로 평범 옵션 라벨 비용 회피.
3. **Clean break**: Pydantic `extra='forbid'` + `Field(min_length=1)`로 legacy `response` 필드 + 빈 `decisions` 모두 422.
4. **builder_v3/ 디렉토리 책임 분리**: graph + state + nodes 단일 책임, wire 어댑터/변환은 services 가 책임.

## ADR-012 마이그레이션 종료

| Phase | 상태 |
|-------|------|
| 0 분석 + ADR | ✅ main |
| 1 Backend 인프라 | ✅ main (PR #127) |
| 2 Wire dual-path transition | ✅ main (PR #128) |
| 3 Legacy wire 제거 clean break | ✅ main (PR #129) |
| 4 ask_user 검토 (옵션 A 최종) | ✅ main (PR #130) |
| **5 Builder v3 wire 통일** | ✅ **본 PR** |

## 회귀 검증 결과 (M5)

- backend 게이트 4종 + 신규 가드 16건 PASS
- frontend 게이트 3종 + 어댑터 retire 잔재 0
- builder_v3 기존 시나리오 (test_phase2_to_phase3_with_intent_confirmed_via_resume 등) 모두 보존

## 사용자 수동 검증 요청 (PR 머지 후)

- [ ] `/agents/new/conversational` 페이지 진입 → Phase 2 ask_user 옵션 선택 → 다음 phase 진행
- [ ] Phase 6 image_choice → "건너뛰기" 또는 "생성하기" 선택 → 정상 분기 (JSON.parse fallback 핵심)
- [ ] Phase 6 image_approval → "확정/재생성/건너뛰기" → phase 7 이동
- [ ] 빌더 전체 흐름 → 에이전트 생성 완료

## 검증 명령

```
cd backend && uv run alembic upgrade head && uv run ruff check . && uv run pytest tests/ && uv run pyright app/ tests/
cd frontend && pnpm lint && pnpm test --run && pnpm build
```

## 커밋 시 주의

스코프 외 catalog 자동 갱신 파일 staging 제외:
- `backend/app/data/model_catalog/{catalog,fetch_metadata}.json`
- `backend/app/data/model_catalog/sources/{ai_model_list,openrouter_models,pydantic_genai_prices}.json`

## W3-out 잔여 follow-up (트리거 도달 대기)

- 🟠 cross-tenant LRU sub-cap (인증 도입 PR 함께)
- 🟡 multi-worker (Redis pub/sub) — broker registry 재설계
- 🟡 `evict_expired` dirty flag — multi-worker 후
- 🟡 `events_chunks` 별도 테이블 — turn 5000+ events 도달 시
