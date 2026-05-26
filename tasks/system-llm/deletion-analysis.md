# 삭제 분석 보고서 — S1 (Musk Step 2)

**작성**: 베조스 (QA) · 2026-05-26
**대상**: ADR-019 결정2(.env fallback 제거, DB 단일 source) 적용 시 제거/단절 대상
**스캔**: Explore 서브에이전트 4건 위임 (config read 경로 / functools.cache / resolve_system_api_key 호출부 / 깨질 테스트)

---

## 1. config.py 모델 설정값 — 런타임 read 경로 (단절 대상)

### 정의 위치 (config.py)
| 속성 | 라인 | 기본값 |
|------|------|-------|
| `builder_model_provider` | 105 | `"anthropic"` |
| `builder_model_name` | 106 | `"claude-sonnet-4-6"` |
| `builder_fallback_provider` | 107 | `"openai"` |
| `builder_fallback_name` | 108 | `"gpt-5.4"` |
| `assistant_model_provider` | 109 | `"anthropic"` |
| `assistant_model_name` | 110 | `"claude-sonnet-4-6"` |
| `image_gen_model` | 119 | `"google/gemini-3.1-flash-image-preview"` |

> ADR-019 결정2: **config 상수 자체는 시드 기본값 참조용으로 잔존 가능**. 끊어야 할 것은 아래 **런타임 read 경로**다.

### 런타임 read 경로 (총 18개 포인트, 4개 파일)

**builder_model_* / builder_fallback_*** — `agent_runtime/builder/sub_agents/helpers.py`
- L71-73: `_get_builder_model()` → `create_chat_model(settings.builder_model_provider, settings.builder_model_name, api_key=PROVIDER_API_KEY_MAP.get(...))`
- L81-90: `_get_fallback_model()` → fallback==primary 비교 + `create_chat_model(settings.builder_fallback_provider, settings.builder_fallback_name, ...)`
- L138-139: fallback provider/name 재참조

**assistant_model_*** — `agent_runtime/assistant/assistant_agent.py`
- L74,77-78: `resolve_system_api_key(db, settings.assistant_model_provider)` + `create_chat_model(settings.assistant_model_provider, settings.assistant_model_name, ...)`

**image_gen_model** — 2개 파일
- `agent_runtime/builder_v3/image_gen.py:112`: `"model": settings.image_gen_model`
- `services/image_service.py:125`: `"model": settings.image_gen_model`

### 별도 분류: base_url (ADR-019 — credential payload에서 추출)
`image_gen_base_url` (config.py:118) 런타임 read 2곳:
- `builder_v3/image_gen.py:133`: `f"{settings.image_gen_base_url}/chat/completions"`
- `services/image_service.py:119`: `f"{settings.image_gen_base_url}/chat/completions"`

→ 이 두 경로는 image role을 `resolve_system_model("image")`로 전환 시 **credential payload의 base_url로 대체**되어야 한다. (M3 배선 담당=젠슨)

---

## 2. `@functools.cache` 제거 영향 (helpers.py)

| 함수 | 라인 | 캐시 키 | config read | 제거 시 비용 |
|------|------|--------|-------------|-------------|
| `load_prompt(filename)` | 37 | filename | 없음(파일시스템) | **없음** — 모듈 import 시 static 바인딩, 런타임 재호출 안 함. 제거해도 영향無 |
| `_get_builder_model()` | 68 | 없음(싱글턴) | builder_model_provider/name + API_KEY_MAP | LLM 인스턴스 생성 ~5-10ms/호출 (httpx+SSL) |
| `_get_fallback_model()` | 78 | 없음(싱글턴) | builder_fallback_provider/name + API_KEY_MAP | 동일 |

**호출부**: helpers.py 내부 L176-177, L226-227 (`invoke_with_json_retry`, `invoke_for_text`). 외부는 sub_agents 4종(intent_analyzer, tool_recommender, middleware_recommender, prompt_generator)이 위 public 함수 경유.

**판단**:
- ADR-019 결정5대로 `@functools.cache`는 **반드시 제거**(런타임 설정 변경 반영). 현재 싱글턴 캐시는 설정 변경이 process 재시작 전까지 무시되는 버그 소지.
- 제거 시 두 함수는 **async + db 인자 추가**(resolver 호출) 필요 → 시그니처 변경. 호출부 L176/226도 await로.
- 비용 완화: ADR-019 권고대로 **`updated_at` 기반 경량 캐시** 도입 권장(없으면 builder 요청마다 LLM 인스턴스 재생성). 단 이건 젠슨 구현 판단 — QA 관점에선 "캐시 제거 + 무효화 메커니즘 부재 시 성능 회귀" 리스크로 표시.

---

## 3. `resolve_system_api_key` 호출부 — 유지 vs `resolve_system_model` 전환

`system_credential_resolver.py`: `async resolve_system_api_key(db, provider) -> str|None` (ENV→is_system credential→None, api_key/token 키 지원). **ADR-013 호환 위해 함수 자체는 유지**(결정3).

| 호출부 | 라인 | 기능 | 전환 판단 |
|--------|------|------|----------|
| `assistant_agent.py` | 73-75 | assistant | **resolve_system_model("text_primary")로 전환** — provider/model_name이 config 정적이라 끊어야 함. base_url도 함께 필요 |
| `image_service.py` | 102 | image | **resolve_system_model("image")로 전환** ⚠️ |
| `builder_v3/image_gen.py` | 49 | image (가용성 체크) | 유지 검토 — bool 체크용. 아래 주석 |
| `builder_v3/image_gen.py` | 96 | image (생성) | **resolve_system_model("image")로 전환** ⚠️ |

> ⚠️ **Explore #3과 베조스 판단 차이(중요)**: Explore는 "image_gen은 base_url/model이 settings에 정적이라 api_key만 필요 → resolve_system_api_key 유지"로 결론냈다. **그러나 ADR-019 결정2는 `image_gen_model`을 런타임 read 제거 대상으로 명시**하고, 결정5 표는 image를 `resolve_system_model("image")`로 전환하라고 한다. 따라서 image도 **DB(image role)에서 model_name+base_url+api_key를 받아야** ADR과 일치한다. `resolve_system_api_key` "유지"는 ADR 위반.
>
> 단 `image_gen.py:49`의 **가용성 체크(`is_image_generation_available`)**는 "image role이 configured인가"를 묻는 것이므로, `resolve_system_model`이 던지는 `SystemModelNotConfiguredError`를 try/except로 감싸거나 setting의 configured 플래그를 확인하는 형태로 전환 권장. api_key 존재 여부만 보던 로직은 부정확해짐.

**resolve_system_api_key 유지 사유**: ADR-013 다른 경로(사용자 에이전트 LLM key 우선순위) 호환. system 모델 3슬롯(builder/assistant/image)은 전부 `resolve_system_model`로 이동 → 결과적으로 system 모델 흐름에서 `resolve_system_api_key` 직접 호출은 **0개로 단절**되어야 정상.

---

## 4. 깨질 기존 테스트

| 파일 | 라인 | 깨지는 이유 | 심각도 |
|------|------|-----------|--------|
| `test_builder_v3.py` | image 흐름 전반 | `settings.image_gen_model` fallback 제거 → 미설정 시 런타임 에러(명시적 `SystemModelNotConfiguredError`로 바뀜) | CRITICAL |
| `test_builder_sub_agents.py` | ~338-389 (model getter), ~351/377 (mock) | `_get_builder_model/_get_fallback_model`이 async+db 시그니처로 변경 → 동기 mock 깨짐, cache 의존 깨짐 | HIGH |
| `test_assistant_agent.py` | ~77,119,144 | `resolve_system_api_key` patch → `resolve_system_model`로 교체 필요 | HIGH |
| `test_assistant_agent.py` | ~14-49 (cache_clear 호출) | `@functools.cache` 제거로 `.cache_clear()` 메서드 소멸 → AttributeError | MEDIUM |
| `conftest.py` | ~60-82 (`_stub_llm_credential_resolution`) | system model resolver 미패치 → builder/assistant 테스트에서 DB 미설정 에러 | HIGH |

> 라인번호는 Explore 추정치 — 젠슨/구현자가 실제 파일에서 재확인 필요. 패턴(무엇이 왜 깨지는가)은 신뢰 가능.

### 신규 필요 테스트 (요약)
- `tests/test_system_llm_settings.py` (신규): role UNIQUE, CHECK 제약, credential SET NULL, CRUD
- `test_system_credential_resolver.py` 확장: `resolve_system_model` configured/미설정(`SystemModelNotConfiguredError`)/base_url None시 canonical pin/image role
- conftest: system model resolver stub fixture 추가

---

## § 즉시 삭제 가능
- helpers.py `_get_builder_model`/`_get_fallback_model`의 `@functools.cache` (결정5, 런타임 설정 반영 차단 버그 소지)
- system 모델 흐름의 `settings.builder_model_*`/`assistant_model_*`/`image_gen_model` **런타임 read** (config 상수 정의는 시드 참조용 잔존)

## § 삭제 검토 필요 (사티아/젠슨 확인)
- `image_gen.py:49` 가용성 체크: api_key 존재→configured 체크로 의미 전환 (단순 삭제 아님)
- `@functools.cache` 제거 후 **updated_at 기반 캐시 미도입 시 성능 회귀** — 완화책 동반 여부 결정 필요
- `resolve_system_api_key` 함수 자체: ADR-013 호환 위해 유지하되 system 모델 흐름 호출부는 전부 0으로

## § 단순화 제안
- image_service.py와 builder_v3/image_gen.py가 base_url+model+api_key 획득 로직 중복 → `resolve_system_model("image")` 단일 진입점으로 통합
