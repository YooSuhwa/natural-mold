# 작업 인계 — chat model factory provider quirks 분리 (ADR-014)

> 새 세션 첫 행동: 본 파일 + `docs/design-docs/adr-014-chat-model-factory-strategy.md` 참조.

## 마지막 상태

- 브랜치: `refactor/chat-model-factory-strategy` (PR #141 open, main `d8744bb` 분기)
- 직전 머지: PR #140 (credential_resolution log level WARNING→INFO)
- backend: pytest **891** / pyright 0/0 / ruff clean / alembic OK
- frontend: 변경 없음 (backend-only refactor)

## 이번 세션 변경

**PR #140** — `credential_resolution.py:83` `logger.warning` → `logger.info` (HANDOFF #3 완료)

**PR #141** — chat model factory provider quirks 분리 (ADR-014, HANDOFF #1+#5 완료)
- `_apply_{anthropic,gpt5,openai_compatible_base_url,openai_ssl_clients}` 4개 helper 추출
- `is_gpt5_family` 공개 → `model_test.py` 사일로 사본 제거 (단일 진실 공급원)
- `_completion_token_cap_kw` 제거 (`_apply_gpt5_quirks` 흡수)
- 공개 API 시그니처 100% 보존

## 남은 follow-up (HANDOFF #1/#3/#5 완료, #2/#4 미착수)

### 코드 품질 (우선순위 순)

1. **Toast 스팸 dedup** — 🟡 Medium / S (2~3h)
   - 한 stream 다중 에러 시 중복 토스트. sonner duplicate suppression
2. **`Model.default_credential` 조건부 lazy load** — 🟡 Medium / S (1~2h)
   - `agent.llm_credential` set 시 불필요한 +1 round-trip 제거
3. ~~**GPT-5 family helper 통합**~~ — ✅ PR #141
4. ~~**`create_chat_model` 책임 분리**~~ — ✅ PR #141
5. ~~**`credential_resolution` WARNING 강등**~~ — ✅ PR #140

### W3-out 잔여 (외부 트리거 대기 — 지금 손대지 말 것)

- 🟠 cross-tenant LRU sub-cap (인증 도입 PR과 함께)
- 🟡 multi-worker (Redis pub/sub)
- 🟡 `evict_expired` dirty flag (multi-worker 후)
- 🟡 `events_chunks` 별도 테이블 (turn 5000+ 시)

## 추천 진입 순서

1. **#1 (Toast dedup)** — UX 즉시 개선, frontend-only
2. **#2 (lazy load)** — 성능 자투리, backend-only

남은 follow-up 모두 S 작업. 묶어서 1 PR 도 가능하나 도메인이 달라(frontend vs backend) 분리 권장.

## 보존 영역 (수정 금지)

- `backend/app/agent_runtime/builder_v3/**` (ADR-012 종료, native interrupt)
- `backend/app/agent_runtime/middleware_registry.py:DEEPAGENT_AUTO_INJECTED_TYPES`
- `backend/app/agent_runtime/tools/ask_user.py` (옵션 A 최종)
- `backend/app/agent_runtime/credential_resolution.py:resolve_llm_api_key_for_agent` (tiered policy)
- `backend/app/agent_runtime/model_factory.py:_apply_*` helpers (ADR-014 strategy)
- `backend/app/services/builder_service.py:decisions_to_builder_response` (Phase 5 router 어댑터)

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
