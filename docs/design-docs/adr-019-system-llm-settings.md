# ADR-019: System LLM Settings (역할별 모델 선택 + base_url 주입)

- **상태**: 제안됨 (2026-05-26)
- **DRI**: chester
- **관련**: ADR-005 (Builder/Assistant), ADR-013 (Service LLM Key from Credentials), ADR-014 (Chat Model Factory)
- **영역**: `app/models/system_llm_setting.py`(신규), `app/services/system_credential_resolver.py`, `app/agent_runtime/assistant/assistant_agent.py`, `app/agent_runtime/builder/sub_agents/helpers.py`, `app/services/image_service.py`, `app/agent_runtime/builder_v3/image_gen.py`, `app/routers/system_llm_settings.py`(신규), `frontend/src/app/.../system-llm`(신규)

---

## § 맥락

System 기능(Builder, Assistant, 이미지 생성)이 호출하는 LLM 모델은 현재 **`.env`(`config.py:104-119`)에 하드코딩**되어 있다:

```python
builder_model_provider: str = "anthropic"
builder_model_name: str = "claude-sonnet-4-6"
builder_fallback_provider: str = "openai"
builder_fallback_name: str = "gpt-5.4"
assistant_model_provider: str = "anthropic"
assistant_model_name: str = "claude-sonnet-4-6"
image_gen_base_url: str = "https://openrouter.ai/api/v1"
image_gen_model: str = "google/gemini-3.1-flash-image-preview"
```

이로 인해 두 가지 한계가 있다:

1. **운영자가 UI에서 system 모델을 바꿀 수 없다** — `.env` 수정 + 재시작 필요.
2. **base_url 주입 경로가 없다** — `resolve_system_api_key()`는 api_key만 반환하고(`system_credential_resolver.py:34`), Builder/Assistant는 `create_chat_model()` 호출 시 base_url을 넘기지 않는다(`assistant_agent.py:76`). 따라서 **LiteLLM proxy 같은 self-hosted OpenAI-compatible 엔드포인트로 system 기능을 태울 수 없다.**

일반 사용자 에이전트는 이미 `Model.base_url` 컬럼을 통해 openai_compatible/LiteLLM을 지원한다(`conversations.py:104`). System 흐름만 갭이 남아 있다.

**요구사항**: 운영자가 한 화면에서 텍스트 primary / 텍스트 fallback / 이미지 3개 슬롯의 모델을 선택할 수 있어야 하고, 각 슬롯은 openai / anthropic / openrouter / litellm(openai_compatible) 어떤 provider든 지정 가능해야 한다.

---

## § 결정

### 결정 1: `system_llm_settings` 테이블 신설 (role별 모델 선택 저장)

System 모델 선택은 **싱글턴 성격**(운영자 1조직 = 슬롯 1세트)이므로 role을 키로 하는 테이블을 둔다.

| 컬럼 | 타입 | 설명 |
|------|------|------|
| `id` | UUID PK | |
| `role` | str(40) UNIQUE | `text_primary` / `text_fallback` / `image` |
| `credential_id` | UUID FK → credentials(id) ON DELETE SET NULL, nullable | 선택된 System Credential. provider/key/base_url의 출처 |
| `model_name` | str(200), nullable | discover로 불러온 모델 식별자 |
| `updated_at` | datetime | |

- `user_id` 컬럼 없음 — system 전역 설정. (RLS/멀티테넌시는 후속 과제)
- `credential_id`는 **반드시 `is_system=True` credential**만 허용 (라우터에서 검증).
- provider는 별도 저장하지 않고 **credential의 `definition_key`에서 파생** → 진실 공급원 단일화.

### 결정 2: `.env` fallback 제거 — DB가 단일 source

`builder_model_*`, `assistant_model_*`, `image_gen_model`은 더 이상 런타임에서 읽지 않는다. (config 상수는 시드 기본값 참조용으로만 잔존)

- **부팅 시드**: `system_llm_settings`에 3개 role row를 `credential_id=NULL, model_name=NULL`로 보장 생성(idempotent). 기본값을 강제로 박지 않는다 — 운영자가 화면에서 선택.
- **미설정 시 동작**: role의 `credential_id` 또는 `model_name`이 NULL이면 해당 system 기능 호출 시 `SystemModelNotConfiguredError(role)` → 사용자에게 "운영자가 System LLM 설정을 완료해야 합니다" 메시지. (조용한 .env fallback 없음 — 설정 누락이 숨지 않음)

### 결정 3: resolver 확장 — `resolve_system_model(role)`

`resolve_system_api_key(provider)`는 ADR-013 호환을 위해 유지하되, 신규 함수를 추가한다:

```python
async def resolve_system_model(
    db: AsyncSession, role: str
) -> ResolvedSystemModel:  # (provider, model_name, api_key, base_url)
    setting = await _get_setting(db, role)
    if setting is None or setting.credential_id is None or not setting.model_name:
        raise SystemModelNotConfiguredError(role)
    cred = await credential_service.get_system(db, setting.credential_id)
    payload = await credential_service.decrypt_with_external(cred.data_encrypted)
    return ResolvedSystemModel(
        provider=cred.definition_key,                      # anthropic|openai|openrouter|openai_compatible
        model_name=setting.model_name,
        api_key=payload.get("api_key") or payload.get("token"),
        base_url=payload.get("base_url"),                  # openai_compatible/openrouter → 값, 그 외 None
    )
```

- `base_url`이 None이면 `model_factory._apply_openai_compatible_base_url`이 canonical endpoint를 pin(openai/openrouter). openai_compatible은 credential의 base_url 필수(definition에서 `required=True`).

### 결정 4: credential 등록은 기존 화면, 신규 화면은 "선택"만

- **권한**: System LLM 설정의 모든 API 엔드포인트는 `Depends(require_super_user)`로 보호하고, 프론트 화면도 운영자 메뉴에만 노출한다(System Credentials와 동일 권한 모델). 일반 사용자에게는 메뉴·라우트·API 모두 비노출.
- credential CRUD는 기존 System Credentials 화면(`/settings/system-credentials`) 유지.
- 신규 **System LLM 설정 화면**: 슬롯 3개. 각 슬롯에서
  1. System Credential 선택 (드롭다운, `is_system=True` LLM credential)
  2. `POST /api/credentials/{id}/discover-models`로 모델 목록 로드 (기존 API 재사용)
  3. 모델 선택 → `system_llm_settings` 저장

### 결정 5: 배선 — config 호출부를 resolver로 교체

| 호출부 | 변경 |
|--------|------|
| `assistant_agent.py:build_assistant_agent` | `resolve_system_model("text_primary")` → `create_chat_model(provider, model_name, api_key, base_url)` |
| `builder/sub_agents/helpers.py:_get_builder_model` | `text_primary` |
| `builder/sub_agents/helpers.py:_get_fallback_model` | `text_fallback` |
| `image_service.py` / `builder_v3/image_gen.py` | `resolve_system_model("image")` → base_url + model_name 사용 |

- `_get_builder_model`/`_get_fallback_model`의 `@functools.cache`는 제거(설정이 런타임에 바뀌므로). 캐시가 필요하면 설정 `updated_at` 기반 무효화.

---

## § DB 마이그레이션

Alembic **M45** — `system_llm_settings` 테이블 생성 + 3개 role row 시드(NULL credential). downgrade는 drop table.

`CHECK (role IN ('text_primary','text_fallback','image'))` + `UNIQUE(role)`.

---

## § 영향 / 리스크

- **Breaking**: 머지 후 운영자가 System LLM 설정 화면에서 3슬롯을 선택하기 전까지 Builder/Assistant/이미지 생성이 동작하지 않는다(.env fallback 제거 결정에 따른 의도된 동작). 배포 노트에 "운영자 설정 필수" 명시.
- **캐시 제거**로 builder 모델 인스턴스가 호출마다 생성될 수 있음 → `updated_at` 기반 경량 캐시로 완화.
- credential 삭제 시 `SET NULL` → 해당 슬롯이 미설정 상태로 전이, 다음 호출에서 명확한 에러.

## § 대안 (기각)

- **A. .env fallback 유지** — 사용자가 명시적으로 거부. 설정 누락이 조용히 숨는 것을 원치 않음.
- **B. provider를 테이블에 별도 저장** — credential.definition_key와 이중 진실 공급원이 되어 불일치 위험. 기각.
- **C. credential 등록을 신규 화면에 통합** — 화면이 무거워지고 기존 System Credentials 화면과 책임 중복. 기각(결정 4).
