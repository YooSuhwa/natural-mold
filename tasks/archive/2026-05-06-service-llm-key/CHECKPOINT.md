# CHECKPOINT — Service LLM Key from Credentials (UX 갭 해소)

> 마일스톤 게이트 — 사티아 소유. 팀원 완료 보고 시 검증 → done-when 충족 시 done 마킹.
> 브랜치: `feat/service-llm-key-from-credentials` (main `4fee88c`에서 분기)
> 사용자 결정 (2026-05-06): 옵션 B — credentials UI 등록 LLM 키를 builder/assistant sub-agent 도 사용 가능. UX 갭 해소.

---

## 핵심 스코프

**문제**: Builder/Assistant sub-agent는 `settings.{provider}_api_key` (.env / OS env) 만 사용. 사용자가 `/credentials` UI 에 등록한 LLM provider 키는 일반 chat agent (`Agent.llm_credential`) 전용이라 builder 가 못 씀. 사용자 mental model("credentials = 단일 진실 공급원") 과 어긋남.

**해결**: lifespan startup 시점 + credential CRUD 변경 시 credentials 테이블의 LLM provider 키를 `_ENV_FALLBACK` dict 로 동기화. `.env` 키 있으면 우선 (backward compat).

| 영역 | 결정 |
|------|------|
| `model_factory.py:_ENV_FALLBACK` 동기화 | ✅ 신규 — credentials → dict 주입 |
| `main.py` lifespan | ✅ startup hook 으로 1회 동기화 |
| credential CRUD API | ✅ invalidate hook (재시작 없이 반영) |
| `.env` 키 우선순위 | ✅ env 가 있으면 우선 (backward compat) |
| Frontend 변경 | ❌ 0 (mental model에 맞추는 backend-only fix) |
| `Agent.llm_credential` (end-user agent) 경로 | 🔒 보존 (이미 정상 동작) |

회귀 위험 최소화: env-only fallback 동작은 유지. credentials 통합은 *추가* 경로.

---

## M0: 거버넌스 초기화 (사티아 DRI)
- [x] 브랜치 `feat/service-llm-key-from-credentials` 생성 (main `4fee88c`)
- [x] CHECKPOINT.md 작성
- [ ] AUDIT.log 진입 기록
- 검증: `git branch --show-current`
- done-when: 새 브랜치 + CHECKPOINT + AUDIT 항목
- 상태: in-progress

## M1: 의존성 분석 (베조스 DRI)
- [ ] credentials 테이블 LLM provider 키 식별 패턴 (definition_key 매핑 — anthropic/openai/google/openrouter)
- [ ] 기존 `Agent.llm_credential` 복호화 경로 추적 — 재사용 가능한 helper 식별
- [ ] credential CRUD API 위치 (POST/PATCH/DELETE) + invalidate hook 삽입 지점
- [ ] `_ENV_FALLBACK` 호출처 매핑 (helpers.py / model_factory.py 외)
- [ ] 회귀 가드 후보 시나리오 명세
- 검증: `tasks/credentials-llm-key-sync-analysis.md` 존재
- done-when: 의존성 보고서 + invalidate hook 위치 + 회귀 가드 시나리오
- 상태: pending

## M2: 아키텍처 + ADR (피차이 DRI, M1 이후)
- [ ] 신규 ADR `docs/design-docs/adr-013-service-llm-key-from-credentials.md` 작성 — 결정 사유 (사용자 mental model + a7fc92d "런타임 키 격리"와 trade-off)
- [ ] 키 우선순위 결정: env > credentials (또는 credentials > env). backward compat 측 권장 = env 우선
- [ ] invalidate hook 메커니즘 결정 (mutable dict vs lock-protected reload vs callback registry)
- [ ] credentials 의 anthropic/openai/google/openrouter definition_key 매핑 명세
- 검증: `test -f docs/design-docs/adr-013-service-llm-key-from-credentials.md`
- done-when: ADR 작성 + 우선순위 + hook 디자인 + provider 매핑
- 상태: pending

## M3: Backend 구현 (젠슨 DRI, M2 이후)
- [ ] `app/services/credential_service.py` (또는 신규) `get_provider_keys() -> dict[str, str | None]` async helper — credentials 테이블 LLM provider 별 키 복호화
- [ ] `model_factory.py` `_ENV_FALLBACK` 을 mutable dict 또는 resolver function 으로 변경 + thread-safe sync helper `sync_env_fallback_from_credentials(db)`
- [ ] `main.py` lifespan startup — sync 호출 1회
- [ ] credential CRUD (POST/PATCH/DELETE) 핸들러 — sync 재호출 (또는 callback registry)
- [ ] 신규 가드 ≥3건:
  - `test_lifespan_syncs_credentials_to_env_fallback`
  - `test_credential_create_invalidates_env_fallback`
  - `test_env_key_takes_priority_over_credential` (backward compat)
  - `test_get_provider_keys_decrypts_anthropic` (helper 단위)
- 검증: `cd backend && uv run alembic upgrade head && uv run ruff check . && uv run pytest tests/ && uv run pyright app/ tests/`
- done-when: ruff 0 / pyright 0/0 / pytest 회귀 0 + 신규 가드 ≥3건 PASS
- 상태: pending

## M4: 회귀 검증 + 통합 (베조스 DRI, M3 이후)
- [ ] backend 게이트 4종 + 신규 가드 PASS
- [ ] 사용자 시나리오 검증: credentials 에 anthropic 키 등록 → builder 정상 LLM 호출 (수동 또는 통합 테스트)
- [ ] backward compat: `.env` ANTHROPIC_API_KEY 있으면 그것 우선 사용
- [ ] credential 삭제 후 sync 재호출되어 키 누락 반영
- 검증: 위 항목 모두 통과
- done-when: 게이트 + 사용자 시나리오 + backward compat + invalidate 모두 PASS
- 상태: pending

## M5: HANDOFF (사티아 DRI, M4 이후)
- [ ] HANDOFF.md 작성
- [ ] progress.txt 학습 entry
- [ ] AUDIT PROJECT_DONE
- 상태: pending

---

## 보존 영역 (수정 금지)

- `Agent.llm_credential` 복호화 경로 (chat_service / agent_runtime 의 end-user agent 흐름)
- `credentials` 테이블 schema (변경 0)
- `Cipher` / `key_provider` (M1 산출, 보존)
- frontend `/credentials` 페이지 (UI 변경 0)

## 회귀 위험 최소화

1. **`.env` 우선** — backward compat. 기존 사용자 영향 0
2. **mutable dict 동기화** — 기존 `PROVIDER_API_KEY_MAP = _ENV_FALLBACK` alias 그대로 유지, dict 내용만 갱신
3. **lifespan startup 1회 + CRUD invalidate** — credential 변경 즉시 반영
4. **신규 가드 ≥3건** + 사용자 시나리오 검증 (M4)
5. **end-user agent 경로 보존** — `Agent.llm_credential` 흐름은 변경 0
