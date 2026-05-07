# M1: LLM Provider Key Sync — Dependency Analysis

**DRI**: Bezos (Quality/Audit)  
**Date**: 2026-05-06  
**Branch**: `feat/service-llm-key-from-credentials` @ 4fee88c  

## 1. Credentials Table LLM Provider Key Identification

### 1.1 Credential Model & definition_key Mapping

**File**: `backend/app/models/credential.py` (lines 18–62)

- **Primary Key**: `id: uuid.UUID` (line 25)
- **User Association**: `user_id: uuid.UUID` (lines 26–28) → `users.id` FK  
- **Provider Identifier**: `definition_key: str[80]` (line 29) — **matches credential definition registry key**  
- **Encrypted Payload**: `data_encrypted: str` (line 33) — Cipher V2 blob (version 1B | salt 32B | authTag 16B | ciphertext)  
- **Active Key Tracking**: `key_id: str[16]` (line 35) — 8-char identifier for active encryption key at creation/update  
- **Field Index Cache**: `field_keys: list[str] | None` (line 37) — Metadata: field names in decrypted data (avoids decrypt for list views)  
- **Status**: `status: str[20]` (line 47) — "active" or rotated state  

### 1.2 LLM Provider definition_key Values

**File**: `backend/app/credentials/definitions/` (14 definition files)

LLM provider definitions matching builder/assistant helper usage:

| Provider | definition_key | File | api_key Field | Category |
|----------|---|---|---|---|
| Anthropic | `anthropic` | `anthropic.py` L9 | `api_key` (FieldKind.PASSWORD, required) L16–22 | `llm` |
| OpenAI | `openai` | `openai.py` L9 | `api_key` + optional `organization` | `llm` |
| Google Generative AI | `google_genai` | `google_genai.py` L9 | `api_key` (FieldKind.PASSWORD) | `llm` |
| OpenRouter | `openrouter` | `openrouter.py` L12 | `api_key` + optional `base_url` | `llm` |
| OpenAI-Compatible | `openai_compatible` | `openai_compatible.py` | `api_key` + `base_url` | `llm` |

**Key insight**: All LLM definitions store the API key in a `data: dict` field named `api_key` (except auxiliary fields like `organization` or `base_url`). The registry normalizes lookup by `definition_key`.

### 1.3 Field Extraction Pattern

**File**: `backend/app/credentials/domain.py` (CredentialDefinition)

Each definition has:
- `key: str` — unique definition identifier (e.g., `"anthropic"`)
- `category: str` — `"llm"` for LLM providers  
- `properties: list[FieldDef]` — schema with `name`, `display_name`, `kind`, `required`  

To identify LLM provider api_key field:
```python
# Pseudo-code
definition = registry.get(credential.definition_key)
if definition and definition.category == "llm":
    for field in definition.properties:
        if field.name == "api_key":
            # This field contains the decrypted key
```

**Registry lookup**: `backend/app/credentials/registry.py` L33–34, `registry.get(key)` → `CredentialDefinition | None`

---

## 2. Existing Agent.llm_credential Decryption Path

### 2.1 Credential Resolution in Chat Service

**File**: `backend/app/services/chat_service.py` (excerpt)

Conversation loading with eager LLM credential:
```python
# L24: selectinload(Agent.llm_credential)
# Result: agent.llm_credential is hydrated Credential object
```

**Decryption call**:
```python
# app/services/chat_service.py (inferred from grep results)
credentials = await credential_service.decrypt_with_external(
    agent.llm_credential.data_encrypted
)
# credentials is dict[str, Any], e.g. {"api_key": "sk-..."}
```

### 2.2 Decryption Helper — Reusable

**File**: `backend/app/credentials/service.py` (lines 40–54)

```python
# Line 40–47: decrypt_data(blob: str) -> dict[str, Any]
def decrypt_data(blob: str) -> dict[str, Any]:
    plaintext = cipher.decrypt(blob, get_keys())
    parsed = json.loads(plaintext)
    if not isinstance(parsed, dict):
        raise ValueError("decrypted credential payload is not an object")
    return parsed

# Line 50–54: decrypt_with_external(blob: str) -> dict[str, Any]
async def decrypt_with_external(blob: str) -> dict[str, Any]:
    raw = decrypt_data(blob)
    return await resolve_external_refs(raw)
```

**Key dependencies**:
- `app.security.cipher.decrypt(blob, keys)` — Cipher V2 decryption
- `app.security.key_provider.get_keys()` — Load decryption keys from config
- `app.credentials.external_secrets.resolve_external_refs(raw)` — Substitute `__external__` refs (vault, env)

### 2.3 System Credential Resolver — Tiered Lookup Pattern

**File**: `backend/app/services/system_credential_resolver.py` (lines 34–54)

Already implements the pattern we need for user credentials:

```python
# Line 39: ENV fallback first
env_key = PROVIDER_API_KEY_MAP.get(provider)  # e.g., settings.anthropic_api_key
if env_key:
    return env_key

# Line 43: System credential second (is_system=True, active, matching provider)
cred = await credential_service.find_system_by_definition(db, provider)
if cred is None:
    return None

# Lines 47–54: Decrypt and extract api_key
payload = await credential_service.decrypt_with_external(cred.data_encrypted)
api_key = payload.get("api_key") or payload.get("token")
return str(api_key) if api_key else None
```

**Reusable helpers**:
- `credential_service.find_system_by_definition(db, definition_key)` — Query for first active system credential
- `credential_service.decrypt_with_external(blob)` — Decrypt + resolve refs
- `.get("api_key")` or `.get("token")` — Field name variants

---

## 3. Credential CRUD API — Hook Insertion Points

### 3.1 POST /api/credentials (Create)

**File**: `backend/app/routers/credentials.py` (lines 130–152)

```python
@crud_router.post("", response_model=CredentialResponse, status_code=201)
async def create_credential(
    payload: CredentialCreate,
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
) -> CredentialResponse:
    # ... validation ...
    cred = await credential_service.create(
        db,
        user_id=user.id,
        definition_key=payload.definition_key,  # ← definition_key injected here
        name=name,
        data=payload.data,
        is_shared=payload.is_shared,
    )
    await db.commit()  # ← Line 150: DB commit
    await db.refresh(cred)
    return _to_response(cred)
```

**Invalidate hook location**: **After line 150 (after commit)** — Sync only if `definition_key` is LLM provider.

### 3.2 PATCH /api/credentials/{credential_id} (Update)

**File**: `backend/app/routers/credentials.py` (lines 165–189)

```python
@crud_router.patch("/{credential_id}", response_model=CredentialResponse)
async def update_credential(
    credential_id: uuid.UUID,
    payload: CredentialUpdate,
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
) -> CredentialResponse:
    cred = await _load_owned(db, credential_id, user.id)
    # ... validation ...
    await credential_service.update(
        db,
        credential=cred,
        actor_user_id=user.id,
        name=payload.name,
        data=payload.data,  # ← api_key may be updated
        is_shared=payload.is_shared,
        status=payload.status,
    )
    await db.commit()  # ← Line 187: DB commit
    await db.refresh(cred)
    return _to_response(cred)
```

**Invalidate hook location**: **After line 187 (after commit)** — Sync only if `definition_key` is LLM provider.

### 3.3 DELETE /api/credentials/{credential_id} (Delete)

**File**: `backend/app/routers/credentials.py` (lines 192–206)

```python
@crud_router.delete("/{credential_id}", status_code=204)
async def delete_credential(
    credential_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
) -> None:
    cred = await _load_owned(db, credential_id, user.id)
    await credential_service.write_audit_log(...)
    await db.delete(cred)
    await db.commit()  # ← Line 206: DB commit
```

**Invalidate hook location**: **After line 206 (after commit)** — Sync only if `definition_key` is LLM provider (remove key from `_ENV_FALLBACK` dict).

### 3.4 System Credentials (Same Pattern)

**File**: `backend/app/routers/credentials.py` (lines 234–256, 270–294, 297–311)

- `create_system_credential` (POST) → hook after line 254  
- `update_system_credential` (PATCH) → hook after line 292  
- `delete_system_credential` (DELETE) → hook after line 310  

---

## 4. _ENV_FALLBACK Usage Map

### 4.1 Definition & Initialization

**File**: `backend/app/agent_runtime/model_factory.py` (lines 54–62)

```python
# Line 54–59: Mutable dict initialized from settings (env vars)
_ENV_FALLBACK: dict[str, str] = {
    "openai": settings.openai_api_key,
    "anthropic": settings.anthropic_api_key,
    "google": settings.google_api_key,
    "openrouter": settings.openrouter_api_key,
}

# Line 62: Backwards-compatible alias
PROVIDER_API_KEY_MAP = _ENV_FALLBACK
```

**Constraint**: `_ENV_FALLBACK` is a **mutable dict**. Keys are provider names (match `definition_key`); values are API key strings or empty.

### 4.2 Usage Sites

| Caller | File | Lines | Usage |
|--------|------|-------|-------|
| Builder helper | `backend/app/agent_runtime/builder/sub_agents/helpers.py` | L73, L85 | `PROVIDER_API_KEY_MAP.get(settings.builder_model_provider)` |
| Fallback model | `backend/app/agent_runtime/builder/sub_agents/helpers.py` | L85 | `PROVIDER_API_KEY_MAP.get(settings.builder_fallback_provider)` |
| create_chat_model | `backend/app/agent_runtime/model_factory.py` | L101 | `_ENV_FALLBACK.get(provider)` fallback for api_key |
| System credential resolver | `backend/app/services/system_credential_resolver.py` | L39 | `PROVIDER_API_KEY_MAP.get(provider)` tier 1 (ENV first) |

**Key insight**: All callers use `.get(provider_name)` where `provider_name` is a string matching `definition_key` (e.g., `"anthropic"`, `"openai"`).

### 4.3 Env Provider Keys Accessor

**File**: `backend/app/agent_runtime/model_factory.py` (lines 127–130)

```python
def env_provider_keys() -> dict[str, str | None]:
    """Return the env-var fallback map. Used by provider_api_keys paths."""
    return {provider: key or None for provider, key in _ENV_FALLBACK.items()}
```

Used by API endpoint(s) that list available env-var keys.

---

## 5. Regression Guard Scenarios

### 5.1 Scenario: Lifespan Sync on Startup

**Test name**: `test_lifespan_syncs_credentials_to_env_fallback`

**Setup**:
- Create database with mock user
- Insert one `Credential` row:
  - `definition_key="anthropic"`
  - `data_encrypted=encrypt({"api_key": "sk-test-123"})`
  - `user_id=mock_user_id`
  - `status="active"`
- Mock `.env` **without** `ANTHROPIC_API_KEY` (to test credentials → dict fallback)

**Fixture pattern**:
```python
@pytest.fixture
async def cred_anthropic(db: AsyncSession):
    cred = await credential_service.create(
        db,
        user_id=TEST_USER_ID,
        definition_key="anthropic",
        name="Test Anthropic",
        data={"api_key": "sk-test-123"},
    )
    await db.commit()
    return cred
```

**Action**: Trigger lifespan startup (call the startup hook function directly or via test client)

**Assert**:
- `_ENV_FALLBACK["anthropic"] == "sk-test-123"`
- Other keys (openai, google, openrouter) remain unchanged from `.env`

### 5.2 Scenario: Credential Create Invalidates Dict

**Test name**: `test_credential_create_invalidates_env_fallback`

**Setup**:
- Start with `_ENV_FALLBACK` empty (or .env keys for non-LLM)
- No pre-existing anthropic credential

**Action**:
```python
response = await client.post(
    "/api/credentials",
    json={
        "definition_key": "anthropic",
        "name": "New Anthropic",
        "data": {"api_key": "sk-new-456"},
    },
)
assert response.status_code == 201
```

**Assert**:
- `_ENV_FALLBACK["anthropic"] == "sk-new-456"` ← Updated after POST
- API response includes the credential ID
- Decryption round-trip verifies data persisted

### 5.3 Scenario: Credential Update Invalidates Dict

**Test name**: `test_credential_update_invalidates_env_fallback`

**Setup**:
- Create anthropic credential with `api_key: "old-key"`
- Sync to `_ENV_FALLBACK["anthropic"] = "old-key"`

**Action**:
```python
response = await client.patch(
    f"/api/credentials/{cred_id}",
    json={
        "data": {"api_key": "new-key-789"},
    },
)
assert response.status_code == 200
```

**Assert**:
- `_ENV_FALLBACK["anthropic"] == "new-key-789"` ← Updated after PATCH
- Old key no longer in dict

### 5.4 Scenario: Credential Delete Clears Dict Entry

**Test name**: `test_credential_delete_invalidates_env_fallback`

**Setup**:
- Create anthropic credential
- Sync to `_ENV_FALLBACK["anthropic"] = "some-key"`

**Action**:
```python
response = await client.delete(f"/api/credentials/{cred_id}")
assert response.status_code == 204
```

**Assert**:
- `_ENV_FALLBACK["anthropic"]` is `None` or `""` (cleared, not absent from dict)
- Accessor `env_provider_keys()` returns `{"anthropic": None, ...}`

### 5.5 Scenario: .env Key Takes Priority (Backward Compat)

**Test name**: `test_env_key_takes_priority_over_credential`

**Setup**:
- Mock `.env` with `ANTHROPIC_API_KEY="env-key-from-settings"`
- Create credential with `api_key: "cred-key"`
- Sync to `_ENV_FALLBACK["anthropic"] = "cred-key"` (credential wins on startup if no env var)

**Variation A** — Both exist:
- At startup, `.env` key is loaded first (settings → `_ENV_FALLBACK`)
- Later, credentials sync updates the dict (overwriting with credential key)

**Variation B** — Env var set during runtime:
- Test that model_factory.create_chat_model() prefers the current value in `_ENV_FALLBACK`
- Mock `settings.anthropic_api_key = "env-override"` after initial load
- Assert: create_chat_model("anthropic", ..., api_key=None) uses dict value, not settings

**Decision** (per CHECKPOINT.md line 20): **env > credentials** for backward compat.
- Startup: Load `.env` into `_ENV_FALLBACK` first (settings layer)
- Startup: Sync credentials, but only if `_ENV_FALLBACK[provider]` is empty or None
- Result: `.env` keys are never overwritten by credentials

**Guard assertion**:
```python
# .env has ANTHROPIC_API_KEY=env-override
# credential has api_key=cred-key
# Expected: builder uses env-override (from settings → _ENV_FALLBACK on init)

model = create_chat_model("anthropic", "claude-3-5-haiku", api_key=None)
# Internally: api_key or _ENV_FALLBACK.get("anthropic") → "env-override"
```

### 5.6 Scenario: Helper Decrypts Anthropic Credential

**Test name**: `test_get_provider_keys_decrypts_anthropic`

**Setup**:
- Create anthropic credential with `api_key: "sk-decrypt-test"`

**Action** (unit test of new helper):
```python
# Proposed: credential_service.get_provider_keys(db, user_id) -> dict[str, str | None]
provider_keys = await credential_service.get_provider_keys(db, user_id=TEST_USER_ID)
```

**Assert**:
- `provider_keys["anthropic"] == "sk-decrypt-test"`
- `provider_keys["openai"]` is `None` (no openai credential for user)
- Decryption succeeded without exception

### 5.7 System Credentials — No Override in _ENV_FALLBACK

**Test name**: `test_system_credentials_do_not_override_env_fallback`

**Setup**:
- Create system credential (is_system=True) with `definition_key="anthropic"`
- Sync logic should NOT touch `_ENV_FALLBACK` for system credentials

**Action**:
- POST/PATCH/DELETE system credential
- Trigger invalidate hook

**Assert**:
- `_ENV_FALLBACK["anthropic"]` unchanged
- Only user credentials (is_system=False) sync to dict

---

## 6. Summary of Dependencies

### Key Files to Modify (M3)

1. **`app/credentials/service.py`** — Add `get_provider_keys(db, user_id) -> dict[str, str | None]`
   - Query for active LLM credentials per user
   - Decrypt and extract api_key field
   - Return dict keyed by definition_key

2. **`app/agent_runtime/model_factory.py`** — Make `_ENV_FALLBACK` mutable dict
   - Add `sync_env_fallback_from_credentials(db) -> None` helper
   - Iterate over LLM provider keys, sync from credentials DB
   - Respect env-var priority (don't overwrite non-None settings values)

3. **`app/main.py` lifespan** (startup) — Call sync once
   - Line 73–160: Add sync call after Bootstrap (line 127)

4. **`app/routers/credentials.py`** — Invalidate hooks in CRUD endpoints
   - POST line 150 → call sync
   - PATCH line 187 → call sync
   - DELETE line 206 → call sync
   - Conditional: only if `definition_key` in LLM provider set

### Key Reusable Helpers (Already Exist)

- `credential_service.decrypt_with_external(blob)` — Decrypt credential payload
- `credential_service.find_system_by_definition(db, definition_key)` — Query pattern
- `registry.get(key) -> CredentialDefinition | None` — Lookup definition by key
- `env_provider_keys()` — Return dict of all env-var keys (for introspection)

### New Helper Required

- **`credential_service.get_provider_keys(db, user_id=None) -> dict[str, str | None]`**
  - If `user_id` provided: return LLM keys for that user (user credentials)
  - If `user_id=None`: return keys from system credentials + user fallback (for system flows)
  - Decrypt and extract api_key from each credential
  - Return dict[provider_name] = api_key or None

---

## Test Fixtures & Mocks

### conftest.py Additions

```python
@pytest.fixture
async def credential_anthropic(db: AsyncSession):
    """User credential for anthropic provider."""
    from app.credentials import service as credential_service
    cred = await credential_service.create(
        db,
        user_id=TEST_USER_ID,
        definition_key="anthropic",
        name="Test Anthropic",
        data={"api_key": "sk-test-anthropic-123"},
    )
    await db.commit()
    return cred

@pytest.fixture
async def mock_env_keys(monkeypatch):
    """Clear .env API keys so credentials dict is authoritative."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "")
    monkeypatch.setenv("OPENAI_API_KEY", "")
    # Re-import settings or mock directly
    from app.config import settings
    monkeypatch.setattr(settings, "anthropic_api_key", "")
    monkeypatch.setattr(settings, "openai_api_key", "")

@pytest.fixture
async def mock_env_keys_priority(monkeypatch):
    """Set .env keys to test priority (env > credentials)."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "env-anthropic-override")
    from app.config import settings
    monkeypatch.setattr(settings, "anthropic_api_key", "env-anthropic-override")
```

### Test Class Structure

```python
class TestEnvFallbackSync:
    """Regression guards for _ENV_FALLBACK synchronization."""

    @pytest.mark.asyncio
    async def test_lifespan_syncs_credentials_to_env_fallback(
        self, db: AsyncSession, credential_anthropic, client: AsyncClient
    ) -> None:
        ...

    @pytest.mark.asyncio
    async def test_credential_create_invalidates_env_fallback(
        self, db: AsyncSession, client: AsyncClient
    ) -> None:
        ...

    @pytest.mark.asyncio
    async def test_credential_delete_invalidates_env_fallback(
        self, db: AsyncSession, credential_anthropic, client: AsyncClient
    ) -> None:
        ...

    @pytest.mark.asyncio
    async def test_env_key_takes_priority_over_credential(
        self, db: AsyncSession, credential_anthropic, mock_env_keys_priority
    ) -> None:
        ...

    @pytest.mark.asyncio
    async def test_get_provider_keys_decrypts_anthropic(
        self, db: AsyncSession, credential_anthropic
    ) -> None:
        ...
```

---

## Notes & Decisions

1. **Credential Query Filter**: Only user credentials (is_system=False) sync to `_ENV_FALLBACK` on CRUD. System credentials (is_system=True) are handled separately by `system_credential_resolver`.

2. **Priority Semantics**:
   - **Startup**: Load `.env` values into `_ENV_FALLBACK` via settings layer; then sync credentials **only if** the dict value is empty/None
   - **Runtime CRUD**: Sync updates the dict unconditionally (allows user to override via credentials UI)
   - **Caller usage** (create_chat_model): `api_key or _ENV_FALLBACK.get(provider)` — explicit api_key always wins

3. **Provider Identifier Normalization**: 
   - `definition_key` in Credential table matches `key` in CredentialDefinition
   - `PROVIDER_MAP` keys in model_factory.py also match (openai, anthropic, google, openrouter, custom, openai_compatible)
   - For sync, filter by category="llm" and map definition_key → _ENV_FALLBACK key

4. **Invalidate Hook Timing**: After `await db.commit()` ensures the row is persisted before dict update; reduces race conditions.

5. **Backward Compat**: `.env` keys are never overwritten by credentials (env takes priority). Existing deployments with ANTHROPIC_API_KEY set will continue using that; users can opt-in by clearing the env var and registering a credential.

---

**Analysis Status**: ✅ Complete — All 5 sections mapped with file:line references and fixture patterns ready for M3 implementation.
