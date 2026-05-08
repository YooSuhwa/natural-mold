# 작업 인계 — 8 PR 머지 완료, 다음 세션 라이브 검증

> 새 세션 첫 행동: 본 파일 + (필요 시) `docs/design-docs/adr-014-chat-model-factory-strategy.md` 참조.

## 마지막 상태

- 브랜치: `main` (모든 작업 머지 완료, 본 파일 갱신용 docs 브랜치만 남음)
- 이번 세션 PR 8건 (#140~#147) **전부 main 머지** ✅
- backend: pytest **908** / pyright 0/0 / ruff clean / alembic m35 OK
- frontend: vitest **286** / lint clean / build PASS

## 이번 세션 PR 8건 (전부 머지)

| PR | 의미 |
|----|------|
| #140 | credential_resolution env fallback WARNING→INFO |
| #141 | chat model factory provider quirks 분리 (ADR-014) |
| #142 | chat toast 한 stream 다중 에러 dedup id |
| #143 | Model.default_credential dead eager-load 제거 |
| #144 | builder confirm MCP 도구 silent drop 차단 |
| #145 | builder skill 인지 + revision 한정 표현 ("이것만") |
| #146 | prompt approval 카드 — 전체 보기 토글 → 내부 스크롤 |
| #147 | agent 삭제 — builder_sessions FK ON DELETE SET NULL |

## PR #145 핵심 변경

빌더 v3 가 Skill 자료를 인지 못하던 갭 + revision 한정 표현 무시 회귀 동시 해소.
- 카탈로그 + 추천 + confirm 모두 Tool/McpTool/Skill 3-way 통합
- `recommend_tools` 에 `revision_message`/`previous_recommendations` first-class 인자
- `tool_recommender.md` 한정 표현 lookup table (이것만/X 빼고/X 대신 Y/카테고리 한정)
- 회귀 가드 13건 — 자세한 내용은 PR #145 설명 참조

## 다음 세션 진입점

1. **라이브 검증 시나리오** (이번 세션 fix 모두):
   - #145: "직원 위치" 에이전트 → skill 카탈로그 노출 + "이것만" 수정 → 정확 반영 → `skill_links` 생성
   - #146: phase5 프롬프트 카드 내부 스크롤
   - #147: 빌더 에이전트 즉시 삭제 → 204 + `builder_sessions.agent_id` NULL 끊김
2. 운영 DB 마이그레이션: `cd backend && uv run alembic upgrade head` (m35 적용)
3. 신규 task 시작 (HANDOFF follow-up + 즉시 버그 모두 소진)

## W3-out 잔여 (외부 트리거 대기 — 지금 손대지 말 것)

- 🟠 cross-tenant LRU sub-cap (인증 도입 PR과 함께)
- 🟡 multi-worker (Redis pub/sub)
- 🟡 `evict_expired` dirty flag (multi-worker 후)
- 🟡 `events_chunks` 별도 테이블 (turn 5000+ 시)

## 보존 영역 (수정 금지)

- `agent_runtime/builder_v3/**` — ADR-012 native interrupt 패턴
- `agent_runtime/middleware_registry.py:DEEPAGENT_AUTO_INJECTED_TYPES`
- `agent_runtime/tools/ask_user.py` (옵션 A 최종)
- `agent_runtime/credential_resolution.py:resolve_llm_api_key_for_agent` (tiered policy)
- `agent_runtime/model_factory.py:_apply_*` helpers (ADR-014)
- `services/builder_service.py:decisions_to_builder_response` (Phase 5 router 어댑터)
- `services/chat_service.py:get_owned_conversation_with_agent` — `Model.default_credential` 추가 금지 (#143)
- `services/builder_service.py:_resolve_tools` — 3-way 시그니처 유지 (#144 #145)

## 검증 명령

```
cd backend && uv run alembic upgrade head && uv run ruff check . && uv run pytest tests/ && uv run pyright app/ tests/
cd frontend && pnpm lint && pnpm test --run && pnpm build
```

## 환경 주의 (사용자 셸)

`~/.zshrc:225` 에 `OPENAI_BASE_URL=https://*.proxy.runpod.net/v1` export. PR #139 + ADR-014 의 canonical endpoint pin 으로 backend 영향 차단 완료.

## 커밋 시 주의

스코프 외 catalog 자동 갱신(6시간 cron) 항상 staging 제외:
- `backend/app/data/model_catalog/{catalog,fetch_metadata}.json`
- `backend/app/data/model_catalog/sources/*.json`
