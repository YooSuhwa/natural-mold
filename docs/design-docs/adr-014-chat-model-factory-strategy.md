# ADR-014: Chat Model Factory — Provider Quirks 분리 (Strategy 패턴 도입)

- **상태**: 승인됨 (2026-05-08)
- **DRI**: 피차이 (System Architect)
- **관련**: PR #139 (chat runtime 회귀 일괄 fix), HANDOFF follow-up #1 + #5
- **영역**: `app/agent_runtime/model_factory.py`, `app/services/model_test.py`

---

## § 맥락

PR #139 회귀 추적에서 드러난 두 갈래의 누적 부채:

1. **GPT-5 family 처리 중복** — `create_chat_model` (런타임) + `_completion_token_cap_kw` (test 표면) 양쪽에 동일한 quirk 분기 (`max_tokens` 거부 → `max_completion_tokens` top-level forward + `temperature` drop) 가 별도 구현. PR #139 에서 한쪽만 고쳐 wire mismatch 회귀 1회 발생 (#137 → #138 → #139 연쇄).
2. **`model_test.py` 의 사일로 사본** — `_is_gpt5_family` / `_GPT5_FAMILY_PREFIXES` 가 `model_factory.py` 와 별도 정의. raw curl 표면(`/api/models/{id}/test/raw`)이 실제 wire 와 drift 가능.

`create_chat_model` 의 본문은 6개 quirk 가 한 함수에 누적되어 있다:
- env-fallback 키 해석
- explicit base_url vs canonical pin
- temperature/top_p/max_tokens 옵션 매핑
- Anthropic temperature+top_p 동시 거부
- GPT-5 family `max_completion_tokens` + temperature drop
- ChatOpenAI 전용 SSL trust store 주입

신규 provider quirk (예: Gemini reasoning, Anthropic thinking) 추가 시 함수가 더 길어지고 test path 와 sync 가 끊길 위험 누적.

---

## § 결정

### 결정 1: kwargs in-place 변형 helper 4개로 분리

|Helper|책임|호출 경로|
|---|---|---|
|`_apply_anthropic_quirks`|temperature+top_p 동시 시 top_p drop|`create_chat_model`|
|`_apply_gpt5_quirks`|max_tokens→max_completion_tokens, temperature drop, default cap 주입|`create_chat_model`(default=4096), `create_chat_model_for_test`(default=200)|
|`_apply_openai_compatible_base_url`|`base_url` 미지정 시 provider canonical pin|양쪽|
|`_apply_openai_ssl_clients`|`ChatOpenAI` 계열만 truststore SSL 주입|양쪽|

**대안 검토**: provider 별 클래스 (e.g. `OpenAIQuirks`, `AnthropicQuirks`) 도 검토했으나 quirk 가 cross-cutting (GPT-5 가 OpenAI 의 sub-family) 이고 호출 측이 두 곳뿐이라 함수형이 이득. 향후 quirk 가 늘면 클래스로 승격 가능.

### 결정 2: `is_gpt5_family` 공개 — 단일 진실 공급원

`_is_gpt5_family` (private) → `is_gpt5_family` (public). `model_test.py` 가 자체 사본 대신 `from app.agent_runtime.model_factory import is_gpt5_family` 로 import 한다.

`_GPT5_FAMILY_PREFIXES` 는 함수 내부 구현 상수로 유지 (외부에서 참조하지 않음).

### 결정 3: `_completion_token_cap_kw` 제거

`_apply_gpt5_quirks(completion_token_default=...)` 가 동일 책임을 흡수. `create_chat_model_for_test` 는 호출 시 `default=TEST_COMPLETION_TOKEN_CAP=200` 을 주입.

### 결정 4: 공개 API 불변

- `create_chat_model(provider, model_name, api_key, base_url, **extra)` — 시그니처 유지
- `create_chat_model_for_test(...)` — 시그니처 유지
- `create_chat_model_with_fallback(...)` — 시그니처 유지
- `TEST_COMPLETION_TOKEN_CAP` (PR #138 에서 public 화) — 유지
- `_TEST_COMPLETION_TOKEN_CAP` alias — 유지 (backward compat)

---

## § 결과

**긍정**:
- GPT-5 quirk 단일 구현 → wire mismatch 회귀 차단
- `model_test.py` 가 런타임과 동일 함수로 family 판정 → raw curl drift 0
- 새 provider quirk 추가 시 helper 한 개 추가만 필요 (open/closed)

**부정 / 제약**:
- 함수 호출 1단 추가 (마이크로 비용 무시)
- helper 4개로 책임 분리되어 짧은 호출 chain 추적 필요 — 책임명이 명시적이라 가독성 손해 < 가독성 이득

**불변**:
- 외부에서 import 하는 모든 심볼 (`PROVIDER_API_KEY_MAP`, `PROVIDER_MAP`, `TEST_COMPLETION_TOKEN_CAP`, `sync_env_fallback_from_credentials`, `env_provider_keys`, `create_chat_model*`) 시그니처/이름 유지
- 기존 test 자산 (`tests/test_model_factory.py:188~270` GPT-5/base_url 가드) 변경 없이 통과해야 함

---

## § 검증

- `tests/test_model_factory.py` — GPT-5 가드 + base_url pin 가드 통과
- `tests/test_credentials_llm_sync.py` — `_ENV_FALLBACK` 동기화 미영향 확인
- `tests/test_model_fallback.py` — `create_chat_model_with_fallback` 체인 미영향 확인
- pyright 0/0, ruff clean

## § 후속

- ADR-015 후보: provider quirk 가 5개 이상 누적되면 strategy 클래스로 승격
- `model_test.py:_provider_wire_shape` 도 helper 호출 패턴으로 정렬 (별도 PR — scope 외)
