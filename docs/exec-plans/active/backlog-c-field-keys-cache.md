# 백로그 C — credentials list N+1 복호화 제거

## Context

`GET /api/credentials`가 각 credential 행의 `data_encrypted` 값을 하나씩 Fernet 복호화 + JSON 파싱해 `field_keys` 배열을 추출한다. DB 쿼리는 1회지만 Fernet 복호화가 N번 발생하는 **N+1 복호화** 구조다.

- `credential_service.extract_field_keys()` (credential_service.py:98-103) → `resolve_credential_data()` → `decrypt_api_key()` 호출
- 라우터 `_to_response()`가 list 결과를 순회하며 매 row마다 호출 (routers/credentials.py:32)
- 100개 credential 기준 ~1-2초 응답 지연 추정, CPU O(n) Fernet 연산

목표: `credentials.field_keys` 비암호화 캐시 컬럼을 추가하여 list 시 복호화 없이 field key 목록을 반환. 응답 스키마는 불변(캐시는 내부 최적화).

**보안 검토:** 캐시는 **key 이름만** 저장 (예: `["api_key"]`, `["client_id","client_secret"]`). 값은 여전히 `data_encrypted`에 Fernet으로 유지. 이미 API 응답에 노출 중이므로 기밀성 악화 없음.

---

## 변경 범위

### 1. 모델 — `backend/app/models/credential.py`

`data_encrypted` 다음에 캐시 컬럼 추가:

```python
from sqlalchemy import JSON

field_keys: Mapped[list[str]] = mapped_column(
    JSON, nullable=True, default=list
)
```

- `sa.JSON()`은 PostgreSQL에서 JSONB로 매핑, SQLite(aiosqlite 테스트)도 호환
- `nullable=True`로 두어 legacy row 대응 (backfill + runtime fallback 병행)

### 2. Service — `backend/app/services/credential_service.py`

#### `create_credential()` (42-66)
라인 55 직후:
```python
encrypted = encrypt_api_key(json.dumps(data.data))
cred = Credential(
    user_id=user_id,
    name=data.name,
    credential_type=data.credential_type,
    provider_name=data.provider_name,
    data_encrypted=encrypted,
    field_keys=list(data.data.keys()),   # ← 추가
)
```

#### `update_credential()` (69-82)
`data.data is not None` 분기에서 동기화:
```python
if data.data is not None:
    cred.data_encrypted = encrypt_api_key(json.dumps(data.data))
    cred.field_keys = list(data.data.keys())   # ← 추가
```
- `name`만 수정되는 경우 `field_keys`는 건드리지 않음

#### `extract_field_keys()` (98-103) — 캐시 우선 + lazy fallback
```python
def extract_field_keys(credential: Credential) -> list[str]:
    """Return cached field_keys; fall back to decryption for legacy rows."""
    if credential.field_keys is not None:
        return credential.field_keys
    try:
        return list(resolve_credential_data(credential).keys())
    except Exception:
        return []
```
- 캐시 히트 → 복호화 0회
- Legacy row (백필 이전) → 기존 경로로 fallback
- 라우터 `_to_response()`는 변경 불필요

### 3. Alembic 마이그레이션 — 신규 파일

`backend/alembic/versions/{NEW}_add_credential_field_keys_cache.py`

- `revision`: 새 ID (예: `m7_add_credential_field_keys`)
- `down_revision = "m6_add_credentials"` (현재 head, 확인 완료)
- `upgrade()`:
  1. `op.add_column("credentials", sa.Column("field_keys", sa.JSON(), nullable=True))`
  2. **Data migration**: 기존 row backfill
     - `bind = op.get_bind()`로 SELECT `id, data_encrypted` from credentials
     - 각 row에서 `app.services.encryption.decrypt_api_key` + `json.loads` 호출 후 `.keys()` 추출
     - `UPDATE credentials SET field_keys = :keys WHERE id = :id`
     - 복호화 실패 시 `[]` (tolerant)
     - ENCRYPTION_KEY 미설정이면 skip (경고 로그)
- `downgrade()`: `op.drop_column("credentials", "field_keys")`

### 4. 테스트 — 신규 `backend/tests/test_credentials.py`

기존에 `test_credentials*.py`가 없음 (credential 커버리지는 `test_tools.py`에 분산). 이번 기회에 credential 전용 테스트 파일 신설:

- `test_create_credential_populates_field_keys` — POST 후 DB row의 `field_keys == list(data.keys())`
- `test_update_credential_syncs_field_keys` — PATCH data 변경 시 `field_keys` 갱신
- `test_update_credential_name_only_preserves_field_keys` — `name`만 수정하면 `field_keys` 불변
- `test_list_credentials_returns_cached_field_keys_without_decrypt` — `decrypt_api_key`를 `unittest.mock`으로 패치해 list 호출 중 호출 횟수 0 검증
- `test_extract_field_keys_fallback_for_legacy_row` — `field_keys=None`인 row를 직접 넣고 `extract_field_keys`가 복호화 경로로 동작하는지 확인

aiosqlite in-memory 사용. 기존 `test_tools.py`의 `_make_credential` 헬퍼 패턴 참고.

---

## 참조 파일

| 경로 | 역할 |
|------|------|
| `backend/app/models/credential.py` | 컬럼 추가 |
| `backend/app/services/credential_service.py` | create/update/extract 수정 |
| `backend/app/routers/credentials.py` | 변경 없음 (확인만) |
| `backend/app/schemas/credential.py` | 변경 없음 (`field_keys` 필드 그대로) |
| `backend/app/services/encryption.py` | `decrypt_api_key` 재사용 (마이그레이션 + fallback) |
| `backend/alembic/versions/m6_add_credentials.py` | 신규 마이그레이션의 down_revision |
| `backend/tests/test_tools.py` | `_make_credential` 헬퍼 패턴 참고 |
| `backend/tests/test_encryption.py` | 암호화 round-trip 예시 |

---

## 재사용 가능한 기존 유틸리티

- `app.services.encryption.encrypt_api_key / decrypt_api_key` — 기존 Fernet 래퍼 (그대로 사용)
- `app.services.credential_service.resolve_credential_data` — fallback 경로에서 그대로 재사용
- `sa.JSON()` — 이미 `agent_tools.config`, `models.input_modalities`, `builder_sessions.*`에서 사용 중인 프로젝트 관례

---

## 검증

```bash
# Backend
cd backend

# 1. 마이그레이션 적용
uv run alembic upgrade head
uv run alembic downgrade -1 && uv run alembic upgrade head   # up/down 왕복

# 2. 린트
uv run ruff check app/services/credential_service.py app/models/credential.py tests/test_credentials.py

# 3. 테스트
uv run pytest tests/test_credentials.py -v     # 신규 테스트
uv run pytest tests/test_tools.py -v           # 회귀 (credential 연동)
uv run pytest tests/test_encryption.py -v      # 회귀
uv run pytest                                  # 전체 540+ 테스트

# 4. 런타임 검증 (수동)
docker-compose up -d postgres
uv run uvicorn app.main:app --reload --port 8001
# POST /api/credentials → 생성
# GET  /api/credentials → field_keys 값 동일성 확인
# PATCH /api/credentials/{id} name 변경 → field_keys 불변 확인
# PATCH /api/credentials/{id} data 변경 → field_keys 갱신 확인
```

**완료 기준 (done-when):**
- 새 마이그레이션 up/down 양방향 성공
- list/create/update 시나리오에서 응답 스키마 동일 (diff 0)
- `test_list_credentials_returns_cached_field_keys_without_decrypt`에서 `decrypt_api_key` 호출 횟수 0
- `pytest` 전체 그린, `ruff` 클린
- Legacy row (backfill 스킵된 경우) fallback 경로 정상 동작

---

## 비범위 (이번 PR에서 제외)

- 관련 없는 성능 개선 (백로그 D `lazy="joined"` → `selectinload` 등)
- `credentials.is_active` 기반 필터링 변경
- 마스킹 sentinel 로직 변경
