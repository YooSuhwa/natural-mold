# 작업 인계 — HANDOFF follow-up 5건 전부 완료

> 새 세션 첫 행동: 본 파일 + (필요 시) `docs/design-docs/adr-014-chat-model-factory-strategy.md` 참조.

## 마지막 상태

- 브랜치: `refactor/model-default-credential-lazy-load` (PR #143 open)
- main 머지 완료: PR #140, #141
- main 머지 대기: PR #142, #143
- backend: pytest **891** / pyright 0/0 / ruff clean / alembic OK
- frontend: vitest **286** (49 files) / lint clean / build PASS

## 이번 세션 PR 4건

| PR | 의미 | 상태 |
|----|------|------|
| #140 | credential_resolution env fallback WARNING→INFO | ✅ merged |
| #141 | chat model factory provider quirks 분리 (ADR-014) | ✅ merged |
| #142 | chat toast 한 stream 다중 에러 dedup id | 🟡 open |
| #143 | chat Model.default_credential dead eager-load 제거 | 🟡 open |

## HANDOFF follow-up 진행 (전부 완료 🎉)

| # | 작업 | PR |
|---|------|-----|
| 1 | GPT-5 family helper 통합 | #141 |
| 2 | Model.default_credential 조건부 lazy load | #143 |
| 3 | credential_resolution WARNING 강등 | #140 |
| 4 | Toast 스팸 dedup | #142 |
| 5 | create_chat_model 책임 분리 | #141 |

## W3-out 잔여 (외부 트리거 대기 — 지금 손대지 말 것)

- 🟠 cross-tenant LRU sub-cap (인증 도입 PR과 함께)
- 🟡 multi-worker (Redis pub/sub)
- 🟡 `evict_expired` dirty flag (multi-worker 후)
- 🟡 `events_chunks` 별도 테이블 (turn 5000+ 시)

## 다음 세션 진입점

1. PR #142, #143 머지 확인 → `/sync` 로 main 동기화
2. 신규 task 시작 (HANDOFF follow-up 모두 소진)

## 보존 영역 (수정 금지)

- `backend/app/agent_runtime/builder_v3/**` (ADR-012 종료, native interrupt)
- `backend/app/agent_runtime/middleware_registry.py:DEEPAGENT_AUTO_INJECTED_TYPES`
- `backend/app/agent_runtime/tools/ask_user.py` (옵션 A 최종)
- `backend/app/agent_runtime/credential_resolution.py:resolve_llm_api_key_for_agent` (tiered policy)
- `backend/app/agent_runtime/model_factory.py:_apply_*` helpers (ADR-014 strategy)
- `backend/app/services/builder_service.py:decisions_to_builder_response` (Phase 5 router 어댑터)
- `backend/app/services/chat_service.py:get_owned_conversation_with_agent` (#143 — `Model.default_credential` 다시 추가하지 말 것, dead eager-load)

## 검증 명령

```
cd backend && uv run alembic upgrade head && uv run ruff check . && uv run pytest tests/ && uv run pyright app/ tests/
cd frontend && pnpm lint && pnpm test --run && pnpm build
```

## 환경 주의 (사용자 셸)

`~/.zshrc:225` 에 `OPENAI_BASE_URL=https://*.proxy.runpod.net/v1` export. PR #139 + ADR-014 의 canonical endpoint pin 으로 backend 영향 차단 완료. 다른 도구는 영향받을 수 있음.

## 커밋 시 주의

스코프 외 catalog 자동 갱신(6시간 cron) 항상 staging 제외:
- `backend/app/data/model_catalog/{catalog,fetch_metadata}.json`
- `backend/app/data/model_catalog/sources/*.json`

이번 세션에서도 위 6개 파일이 working tree dirty 상태로 남아있음 (PR 영향 없음).
