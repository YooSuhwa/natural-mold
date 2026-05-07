# 작업 인계 — Service LLM Key from Credentials (UX 갭 해소)

> 새 세션 첫 행동: 본 파일 + `docs/design-docs/adr-013-service-llm-key-from-credentials.md` 참조.

## 마지막 상태

- 브랜치: `feat/service-llm-key-from-credentials` (main `4fee88c` 분기, **커밋 미수행 — 사용자 승인 대기**)
- backend: **881 PASS** (Phase 5 baseline 865 + 16 신규 가드) / pyright 0/0 / ruff clean / alembic OK
- frontend: 변경 없음 (backend-only fix)

## 변경 요약

**문제**: Builder/Assistant sub-agent 가 `.env` 의 `{provider}_api_key` 만 사용해, `/credentials` UI 에 등록한 LLM provider 키를 못 씀. 사용자 mental model("credentials = 단일 진실") 과 어긋남.

**해결**: lifespan startup + credential CRUD invalidate hook 으로 `_ENV_FALLBACK` dict 동기화. `.env` 키 우선 (backward compat).

## 변경 파일 (5개, +588)

- `backend/app/credentials/service.py` (+81): `LLM_DEFINITION_TO_ENV_KEY` 상수, `is_llm_definition()`, `get_provider_keys(db)` async helper
- `backend/app/agent_runtime/model_factory.py` (+38): `_ENV_DEFAULTS` import-time snapshot, `sync_env_fallback_from_credentials(db)` async helper
- `backend/app/main.py` (+15): lifespan startup sync 호출
- `backend/app/routers/credentials.py` (+24): CRUD 6곳(user 3 + system 3) invalidate hook
- `backend/tests/test_credentials_llm_sync.py` (+430, 신규): 가드 16건

## 핵심 설계 결정 (ADR-013)

1. **우선순위**: `ENV > system credential > user credential > None` — backward compat 보장
2. **Idempotent snapshot-then-layer**: `_ENV_DEFAULTS` 가 import 시 .env 캡처 → 매 sync 마다 dict 리셋 후 credentials 레이어. DELETE 시 별도 pop 로직 없이 자연 반영.
3. **단일 SQL priority**: `order_by(is_system DESC, created_at DESC)` + dict 첫 hit 보존 — N+1 회피
4. **Alias 동일성**: `_ENV_FALLBACK` in-place mutation (객체 교체 X) → `PROVIDER_API_KEY_MAP` alias 보유한 builder helper / system_credential_resolver 변경 0
5. **CRUD short-circuit**: `is_llm_definition()` 가드로 non-LLM credential CRUD 는 decrypt 비용 0
6. **Provider 매핑**: `google_genai → google` 별칭, `openai_compatible` skip (base_url 트리플 필요, 별도 ADR)

## 신규 회귀 가드 16건

- `test_credentials_llm_sync.py` — sync helper 단위, lifespan startup, CRUD hook(POST/PATCH/DELETE × user/system), priority 검증, openai_compatible skip, alias 동일성 잠금

## 사용자 시나리오 검증

- credentials UI 에 anthropic 키 등록 → backend sync → `_ENV_FALLBACK["anthropic"]` 갱신 → builder `_get_builder_model()` 새 키 사용 → builder phase 3 LLM 호출 정상

## Backward Compat

- `.env` 에 `ANTHROPIC_API_KEY` 있으면 그대로 우선
- `.env` 비어있고 credentials 에 키 있으면 credentials 사용 (신규 동작)
- 둘 다 없으면 None (이전과 동일 — silent fail)

## ADR-013 위험 + 완화

| 위험 | 완화 |
|------|------|
| credential rotation 시 sync 누락 | CRUD 6곳 hook + alias 동일성 가드 |
| system vs user credentials 충돌 | SQL ordering 으로 system 우선 명시 |
| a7fc92d "런타임 키 격리" 의도 충돌 | end-user agent (`Agent.llm_credential`) 경로 보존 — 본 PR 은 sub-agent 만 영향 |

## 검증 명령

```
cd backend && uv run alembic upgrade head && uv run ruff check . && uv run pytest tests/ && uv run pyright app/ tests/
```

## 사용자 수동 검증 (PR 머지 후)

- [ ] `.env` 에서 ANTHROPIC_API_KEY 제거 (또는 빈 값) + backend 재시작
- [ ] credentials UI 에 anthropic 키 등록
- [ ] `/agents/new/conversational` 시작 → Phase 3 도구 추천 정상 (LLM 호출 성공)
- [ ] `.env` 에 키 다시 추가 → env 우선 동작 (backward compat)

## 커밋 시 주의

스코프 외 catalog 자동 갱신 파일 staging 제외:
- `backend/app/data/model_catalog/{catalog,fetch_metadata}.json`
- `backend/app/data/model_catalog/sources/{ai_model_list,openrouter_models,pydantic_genai_prices}.json`

## W3-out 잔여 follow-up (트리거 도달 대기)

- 🟠 cross-tenant LRU sub-cap (인증 도입 PR 함께)
- 🟡 multi-worker (Redis pub/sub) — broker registry 재설계
- 🟡 `evict_expired` dirty flag — multi-worker 후
- 🟡 `events_chunks` 별도 테이블 — turn 5000+ events 도달 시
