# ADR-013: Service-side LLM Key from Credentials (Builder/Assistant Sub-agent)

- **상태**: 승인됨 (2026-05-06)
- **DRI**: 피차이 (System Architect)
- **관련**: ADR-005 (Builder/Assistant), ADR-009 (Greenfield Credentials), commit `a7fc92d` (런타임 키 격리)
- **영역**: `app/agent_runtime/model_factory.py`, `app/agent_runtime/builder/sub_agents/helpers.py`, `app/services/system_credential_resolver.py`, `app/routers/credentials.py`, `app/main.py`

---

## § 맥락

현재 구조는 LLM 키 출처가 **3-tier로 분기**되어 있다:

| Caller | Key 출처 | 비고 |
|--------|---------|------|
| End-user agent (chat_service) | `Agent.llm_credential` → `decrypt_with_external` | 정상 동작 |
| System-billed flow (Fix Agent, Image Gen) | `system_credential_resolver`: ENV → `is_system=True` Credential | ADR 이전 도입, ENV→system tiered |
| **Builder / Assistant sub-agent** | `PROVIDER_API_KEY_MAP` (alias of `_ENV_FALLBACK`) — **ENV only** | 본 ADR의 갭 |

Builder/Assistant helper (`builder/sub_agents/helpers.py:73,85`) 는 `PROVIDER_API_KEY_MAP.get(provider)` 만 사용한다. 사용자가 `/credentials` UI 에 `anthropic` 키를 등록해도 builder 는 그것을 보지 않는다.

**사용자 mental model**: "Credentials UI = LLM 키 단일 진실 공급원"  
**현재 코드**: builder 는 `.env` 만 본다 → mental model 위반

`system_credential_resolver` 는 이미 ENV→system credential tiered lookup 패턴을 가지고 있다 (Fix Agent, Image Gen 에서 사용). 본 ADR 의 핵심은 **"이 패턴을 builder/assistant 까지 확장하되, 추가 부담 없이 user credentials 도 fallback에 포함시킬 것인가"** 의 결정.

a7fc92d ("런타임 키 격리") 이후 우리는 `Agent.llm_credential` 흐름과 `_ENV_FALLBACK` 흐름을 의도적으로 분리해 왔다 — end-user 가 자기 키로 빌링되는 경로와 operator 가 빌링되는 경로의 격리. 본 ADR 은 이 격리를 깨지 않으면서 UX 갭을 메운다.

---

## § 결정

### 결정 1: 우선순위 — `ENV > system credentials > user credentials`

```
key resolution for builder/assistant sub-agent (provider P):
  1. settings.{P}_api_key  (env / .env)         ← 있으면 즉시 반환
  2. Credential where is_system=True, definition_key=P
  3. (신규) Credential where is_system=False, definition_key=P, status='active'
       ↳ 동일 user 다중 row 시 created_at DESC LIMIT 1
  4. None                                        ← caller 가 LLM 에러 surface
```

**근거**:
- ENV 1순위 = backward compat. 기존 `.env`-only 배포 영향 0.
- System 2순위 = operator-managed 키가 user 키보다 우선. PoC 단계에서 mock user 단일이라 충돌 거의 없으나, 인증 도입 후에도 일관됨.
- User 3순위 = 사용자 mental model 충족. `/credentials` UI 에 키 등록하면 builder 도 자동 사용.

### 결정 2: 재사용 전략 — `system_credential_resolver` 확장

`system_credential_resolver.py` 의 `resolve_system_api_key()` 를 **확장**하지 않고, **신규 helper** 를 `app/credentials/service.py` 에 추가한다:

```python
# app/credentials/service.py (신규)
async def get_provider_keys(db: AsyncSession) -> dict[str, str | None]:
    """LLM provider → api_key dict. system credentials 우선, user fallback.
    
    Returns dict keyed by _ENV_FALLBACK key (openai/anthropic/google/openrouter).
    .env 우선순위는 호출자가 적용 (sync_env_fallback_from_credentials).
    """
```

**왜 신규 helper 인가**:
- `resolve_system_api_key()` 는 단일 provider 단건 lookup (Fix Agent, Image Gen 패턴). startup sync 는 **bulk** 로 전 provider 한 번에 가져와야 효율적.
- 본 ADR 의 호출처는 "dict 갱신용 bulk reader" 이지 "런타임 단건 resolver" 가 아니다. 의미론적으로 다른 함수.
- `resolve_system_api_key` 는 user credentials 를 **의도적으로 배제**한다 (operator billing). 이 의미를 깨면 안 됨.

### 결정 3: Invalidate Hook — `_ENV_FALLBACK.update()` (mutable dict)

3개 옵션을 비교했다:

| 옵션 | 장점 | 단점 | 결정 |
|------|------|------|------|
| (a) **mutable dict `.update()`** | `PROVIDER_API_KEY_MAP` alias 그대로 유지, 코드 변경 최소, atomic update | startup + CRUD 시점에 명시적 호출 필요 | ✅ **채택** |
| (b) callback registry | 미래 consumer 도 hook 가능 | 과한 추상화 (현재 consumer = builder helper 1곳) | ❌ Musk Step 1 위반 |
| (c) lazy reload-on-read + TTL cache | sync 호출 누락 방지 | 매 호출 DB 조회 잠재적 비용, TTL 동안 stale, 테스트 hook 어려움 | ❌ |

**구현 형태**:
```python
# model_factory.py
def sync_env_fallback_from_credentials(
    cred_keys: dict[str, str | None]
) -> None:
    """Provider별 dict.update(). .env 우선 정책: 기존 truthy 값은 덮지 않음."""
    for provider, key in cred_keys.items():
        if key and not _ENV_FALLBACK.get(provider):
            _ENV_FALLBACK[provider] = key
```

**호출 지점**:
1. `app/main.py` lifespan startup — Bootstrap 직후 1회
2. `app/routers/credentials.py` — POST/PATCH/DELETE 핸들러 `await db.commit()` 직후, `definition_key in {anthropic, openai, google_genai, openrouter, openai_compatible}` 인 경우만

**Thread safety**: CPython GIL + dict `.update()` atomic. dict 객체 교체 (`_ENV_FALLBACK = ...`) 는 `PROVIDER_API_KEY_MAP` alias 가 stale 참조 보유하게 되므로 **금지**.

### 결정 4: Provider definition_key ↔ `_ENV_FALLBACK` Key 매핑

| credential definition_key | `_ENV_FALLBACK` key | 비고 |
|---------------------------|---------------------|------|
| `anthropic` | `anthropic` | 1:1 |
| `openai` | `openai` | 1:1 |
| `google_genai` | `google` | **별칭 매핑 필요** (settings.google_api_key) |
| `openrouter` | `openrouter` | 1:1 |
| `openai_compatible` | — (skip) | base_url 필수, env 단일키로 표현 불가. credential 흐름은 그대로 (chat_service 경로). builder 는 사용 안 함. |

매핑 테이블은 `app/credentials/service.py` 에 상수로 명시:
```python
LLM_DEFINITION_TO_ENV_KEY: dict[str, str] = {
    "anthropic": "anthropic",
    "openai": "openai",
    "google_genai": "google",
    "openrouter": "openrouter",
}
# openai_compatible: builder/assistant 미지원 — Agent.llm_credential 경로만
```

`is_llm_definition(key) -> bool` 헬퍼는 위 dict 의 key 멤버십으로 판단.

---

## § 위험 + 완화

| 위험 | 완화 |
|------|------|
| Credential rotation 시 sync 누락 → builder 가 stale 키 사용 | CRUD 3곳 (POST/PATCH/DELETE) hook 누락 검증 가드 (M3 신규 테스트). `definition_key` 화이트리스트 분기. |
| System vs user credential 충돌 | 결정 1 우선순위 명문화. system 1건 + user N건 동시 존재 시 system 우선. |
| a7fc92d "런타임 키 격리" 와 충돌 | end-user agent (`Agent.llm_credential`) 경로는 **변경 0**. 이 ADR 은 builder/assistant (operator-billed surface) 에만 영향. ADR-005 builder 흐름은 원래 operator-billed 의도였음. |
| `.env` priority 위반 회귀 | `sync_env_fallback_from_credentials` 가 `if key and not _ENV_FALLBACK.get(provider)` 가드. 신규 가드 `test_env_key_takes_priority_over_credential` (M1 §5.5). |
| Multi-user 환경에서 user credentials 의 "어떤 user 키" 모호성 | PoC 단계 (mock user 1명) — 즉시 문제 아님. 인증 도입 시 재검토. ADR §향후 작업에 명시. |
| `openai_compatible` 누락 시 사용자 혼란 | UI 에서 builder/assistant 가 지원하는 provider 목록 명시 (저커버그 영역, 본 ADR 범위 외). |

---

## § 마이그레이션

**Backward Compat**:
- `.env` 에 `ANTHROPIC_API_KEY` 가 있는 사용자: 동작 변경 0. `_ENV_FALLBACK["anthropic"]` 는 startup 시 settings 값으로 채워지고, sync 함수는 truthy 값을 덮지 않음.
- `PROVIDER_API_KEY_MAP` alias 보존 → builder helper (`L73,L85`) 코드 변경 0.

**신규 동작**:
- `.env` 비어있고 `/credentials` UI 에 anthropic 키 등록 시:
  - startup → `_ENV_FALLBACK["anthropic"] = "<credentials key>"`
  - 이후 builder 호출 → `PROVIDER_API_KEY_MAP.get("anthropic")` 가 신규 키 반환

**DB 마이그레이션**: 없음. 스키마 변경 0.

**Frontend 변경**: 없음. UI 는 이미 `/credentials` 로 키 등록 가능 (mental model 부합).

---

## § 향후 작업 (본 ADR 범위 외)

1. 인증 도입 시 user credentials fallback 의 user 선택 정책 (본 ADR 은 mock user 단일 가정).
2. `openai_compatible` builder 지원 — model_name + base_url + api_key 트리플 필요. Settings 확장 또는 별도 ADR.
3. Credential rotation audit log → `_ENV_FALLBACK` sync 이벤트 기록 (운영 가시성).

---

## § 결정 요약

1. **우선순위**: ENV > system credential > user credential > None
2. **재사용**: 신규 helper `credential_service.get_provider_keys(db)` (bulk reader). `resolve_system_api_key` 는 의미 보존 (변경 0).
3. **Hook**: `_ENV_FALLBACK.update()` (mutable dict) — startup 1회 + CRUD 3곳 (`definition_key` LLM 화이트리스트 분기).
4. **매핑**: `anthropic`, `openai` 1:1 / `google_genai → google` / `openrouter` 1:1 / `openai_compatible` skip.
5. **Backward compat**: `.env` priority 보존. 기존 사용자 영향 0.
