# ADR-016 — 멀티유저 인증 도입 (HttpOnly Cookie + JWT + super_user)

## 1. Status & Date

- **Status**: Accepted
- **Date**: 2026-05-09
- **Supersedes / Relates**: ADR-009 (Credentials 그린필드 — `is_system` 추가에 영향), ADR-013 (Service-side LLM Key — 사용자별 credential 우선순위 정책)
- **Branch**: `feature/multiuser-auth`
- **Plan source**: `~/.claude/plans/replicated-crunching-lark.md`

---

## 2. Context

natural-mold는 PoC 단계로 단일 Mock User(`00000000-0000-0000-0000-000000000001`, `demo@moldy.dev`)로 동작한다. 데이터 모델은 이미 `user_id` FK 기반으로 멀티유저를 가정하고 설계되어 있어 **백엔드 격리 인프라는 약 70% 완성** 상태이지만, 다음 다섯 가지 충돌 지점이 실제 다중 사용자 운영을 막고 있다.

### 2.1 주요 충돌 지점 5개

1. **인증 시스템 부재** — `backend/app/dependencies.py:get_current_user()`가 환경변수에서 mock user를 그대로 반환. 토큰/세션 개념 자체가 없음.
2. **프론트엔드 인증 인프라 전무** — `/login`·`/register` 라우트, `middleware.ts`, API 클라이언트 토큰 첨부, CSRF 처리 모두 없음.
3. **시스템 리소스의 mock-user 종속** — `backend/app/seed/bootstrap_from_env.py:bootstrap_credentials_from_env`가 `user_id=mock_user_id`로 credential을 생성 → 모든 사용자가 운영자 키로 LLM 호출하게 됨 (비용 폭주 + 보안 사고).
4. **Google OAuth refresh_token이 글로벌 환경변수** — `settings.google_oauth_refresh_token`로 단일 token을 모든 사용자가 공유. per-user 격리가 불가능.
5. **ON DELETE 정책 불일치** — `agents`, `builder_sessions`, `agent_triggers`의 `user_id` FK는 ondelete 미명시 (PostgreSQL default = NO ACTION). 반면 `tools`, `credentials`, `daily_spend_users`는 CASCADE. 사용자 삭제 시 일관성 없음.

### 2.2 목표

3주 안에 production-ready한 멀티유저 MVP를 만들되, 미래에 **개인 → 팀 → 실 → 회사** 4계층 워크스페이스로 확장 가능한 hook을 설계 단계에서 박아둔다. 지금은 User 단위 테넌시만 구현하고, 코드 시그니처와 컬럼 명명만 미래 친화적으로 유지한다.

---

## 3. Decision

### 3.1 핵심 결정 표

| 항목 | 선택 | 근거 |
|------|------|------|
| 토큰 저장 | **HttpOnly Cookie (Access + Refresh) + CSRF Token in JSON body** | XSS 내성, 검증된 패턴, JS는 토큰 직접 접근 불가 |
| 알고리즘 | **JWT HS256** | 표준, 단일 백엔드 환경에서 비대칭 키 불필요 |
| Access TTL | **60분** | 짧게 유지, refresh로 회전 |
| Refresh TTL | **30일** (DB whitelist 기반 회전) | Redis 미사용 — `refresh_tokens` 테이블 |
| 비밀번호 해싱 | **bcrypt (passlib CryptContext)** | 안전, 검증됨. 서버측 단일 해시 |
| 권한 모델 | **`is_super_user` 단일 boolean 플래그** | MVP 단순화, 향후 RBAC 확장 가능 |
| 테넌시 단위 | **User 단위 (MVP)** + **Workspace 확장 hook** | service layer가 user 객체를 받도록 통일 |
| 인증 방식 | **Email + Password 단독** | OAuth 로그인은 Phase 2 |
| 첫 가입자 | **자동 super_user** (`ALLOW_FIRST_USER_AS_ADMIN=true`) | 부트스트랩 단순화 |
| System credentials 가시성 | **super_user 전용** (조회/사용/관리 모두) | 비용 폭주 + 보안 차단. 일반 사용자는 본인 키 필수 등록 |
| Mock user | **제거** (마이그레이션 스크립트로 신규 super_user에 ownership 이전) | 단일-유저 부트스트랩 코드 모두 제거 |

### 3.2 비채택 안

- **localStorage + Bearer 토큰**: XSS 노출 위험 → 거부.
- **OAuth 로그인 MVP 포함**: SMTP/이메일 검증 인프라 미준비, 일정 압박 → Phase 2로 이연.
- **RBAC (역할 테이블)**: MVP에 과잉 → `is_super_user` 단일 플래그로 충분.
- **Redis whitelist**: 인프라 단순화 → DB 기반 `refresh_tokens` 테이블.
- **이메일 검증/비번 재설정 활성화**: SMTP 미준비 → Phase 2. 단, 컬럼은 미리 추가.

---

## 4. Data Model Specs

### 4.1 `users` 테이블 신규 컬럼

| 컬럼 | 타입 | NULL | Default | 비고 |
|------|------|------|---------|------|
| `hashed_password` | `VARCHAR(255)` | YES | NULL | bcrypt hash. OAuth-only 사용자 대비 nullable |
| `is_active` | `BOOLEAN` | NO | `TRUE` | 비활성화 시 로그인 차단 |
| `is_super_user` | `BOOLEAN` | NO | `FALSE` | 첫 가입자만 자동 TRUE |
| `last_login_at` | `TIMESTAMPTZ` | YES | NULL | 성공 로그인 시 갱신 |
| `last_login_ip` | `VARCHAR(45)` | YES | NULL | IPv6 max 길이 |
| `failed_login_attempts` | `INTEGER` | NO | `0` | 5회 도달 시 잠금 |
| `locked_until` | `TIMESTAMPTZ` | YES | NULL | 잠금 만료 시각 (15분 후) |
| `email_verified_at` | `TIMESTAMPTZ` | YES | NULL | Phase 2 활성화. MVP는 항상 NULL |
| `email_verify_token` | `VARCHAR(64)` | YES | NULL | sparse index. Phase 2 |
| `email_verify_expires_at` | `TIMESTAMPTZ` | YES | NULL | Phase 2 |
| `password_reset_token` | `VARCHAR(64)` | YES | NULL | sparse index. Phase 2 |
| `password_reset_expires_at` | `TIMESTAMPTZ` | YES | NULL | Phase 2 |

인덱스:
- `ix_users_email` (UNIQUE, 이미 존재) — 유지.
- `ix_users_email_verify_token` (sparse, WHERE token IS NOT NULL) — Phase 2 사용.
- `ix_users_password_reset_token` (sparse, WHERE token IS NOT NULL) — Phase 2 사용.

### 4.2 `refresh_tokens` 테이블 (신규)

```sql
CREATE TABLE refresh_tokens (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    token_hash VARCHAR(64) NOT NULL,         -- SHA-256 hex of refresh JWT jti
    issued_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    expires_at TIMESTAMPTZ NOT NULL,
    revoked_at TIMESTAMPTZ NULL,             -- 회전 시 즉시 폐기
    user_agent TEXT NULL,
    ip VARCHAR(45) NULL
);
CREATE UNIQUE INDEX ix_refresh_tokens_token_hash ON refresh_tokens(token_hash);
CREATE INDEX ix_refresh_tokens_user_id ON refresh_tokens(user_id);
CREATE INDEX ix_refresh_tokens_active
    ON refresh_tokens(user_id, expires_at)
    WHERE revoked_at IS NULL;
```

회전 정책:
- `/auth/refresh` 호출 시 기존 row의 `revoked_at = NOW()` 설정 + 새 row 발급. 회전 시 `old.replaced_by_id = new.id`로 체인 링크 (m37).
- 이미 `revoked_at IS NOT NULL`인 token이 다시 들어오면 두 갈래로 분기:
  - **Race (탭 경합)** — `replaced_by_id`가 존재 + 교체본이 active + `revoked_at`이 `settings.refresh_rotation_grace_seconds`(기본 10s) 이내 + 원본 row의 user-agent가 현재 요청과 일치 → 교체본에서 다시 회전하여 새 토큰 발급(체인 연장). 일괄 폐기 안 함. 두 탭 동시 `/refresh` 시나리오 보호 (2026-05-18 회귀 가드).
  - **Replay (실제 공격 의심)** — 그 외 모든 경우(다른 UA, grace 초과, 교체본도 폐기됨 등) → 해당 user의 모든 active refresh를 일괄 revoke. `UPDATE refresh_tokens SET revoked_at=NOW() WHERE user_id=:uid AND revoked_at IS NULL`.
- 만료된 row는 cron으로 GC: `settings.refresh_token_gc_cron`(기본 매일 05:00 UTC)에 `DELETE FROM refresh_tokens WHERE expires_at < NOW() - settings.refresh_token_gc_retention_days days` 실행. 기본 retention 1d로 barely-expired token도 replay 분류 가능. `replaced_by_id` 자기-FK는 `ON DELETE SET NULL` 이라 체인 중간 row 삭제도 안전. 구현: `app/services/refresh_token_gc.py`, `app/scheduler.py::register_refresh_token_gc_job`.
- 회전은 Postgres `SELECT ... FOR UPDATE`로 row를 lock한 뒤 mutation. 두 패자가 동시에 같은 chain head를 rotate하려 하면 늦은 쪽이 lock 해제 후 revoked 상태를 발견하고 체인을 따라 다음 hop으로 전진(최대 `_MAX_CHAIN_FOLLOW`회). 결과: 모든 새 leg가 단일 chain에 linearly linked, orphan active row 없음. SQLite 테스트 환경은 lock 미지원이라 동시성 회귀는 Postgres 통합 테스트에서만 검증 가능. 구현: `auth_service._lock_row` + `rotate_refresh` chain-walk 루프.

### 4.3 `oauth_accounts` 테이블 (Phase 2 자리만 — 지금 만들지 않음)

> **MVP에서는 이 테이블을 만들지 않는다.** 아래는 Phase 2 추가 시 이름·구조 충돌을 피하기 위한 사전 결정.

```sql
-- Phase 2에 추가될 예정. m22_multiuser_auth.py에는 포함하지 않음.
CREATE TABLE oauth_accounts (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    provider VARCHAR(32) NOT NULL,           -- 'google', 'github', ...
    provider_user_id VARCHAR(255) NOT NULL,  -- provider 내부 user id
    email VARCHAR(255) NULL,
    access_token_encrypted TEXT NULL,        -- Cipher V2
    refresh_token_encrypted TEXT NULL,       -- Cipher V2
    token_expires_at TIMESTAMPTZ NULL,
    scope TEXT NULL,
    linked_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (provider, provider_user_id)
);
```

설계 약속:
- 로그인용 OAuth는 도구용 (`google_workspace_tools.py`)과 **분리된 client**로 운영.
- 라우터 prefix는 `/api/auth/oauth/{provider}/start|callback`.
- `User.hashed_password = NULL` + `oauth_accounts` row만 있는 사용자(= OAuth-only) 허용.

### 4.4 `tools.is_system` / `credentials.is_system` 컬럼 추가

| 테이블 | 컬럼 | 타입 | NULL | Default |
|--------|------|------|------|---------|
| `tools` | `is_system` | `BOOLEAN` | NO | `FALSE` |
| `credentials` | `is_system` | `BOOLEAN` | NO | `FALSE` |

CHECK constraint (양 테이블 동일):

```sql
ALTER TABLE tools ADD CONSTRAINT ck_tools_system_user_null
    CHECK ((is_system = FALSE) OR (user_id IS NULL));
ALTER TABLE credentials ADD CONSTRAINT ck_credentials_system_user_null
    CHECK ((is_system = FALSE) OR (user_id IS NULL));
```

→ `is_system=TRUE`이면 반드시 `user_id IS NULL`. 기존 `user_id IS NULL` 관행을 명시적 boolean으로 변환.

### 4.5 FK ON DELETE 정책 매트릭스

| 테이블 | 컬럼 | 현재 | 변경 후 | 근거 |
|--------|------|------|---------|------|
| `agents` | `user_id` | unspecified (= NO ACTION) | **CASCADE** | 사용자 탈퇴 시 본인 에이전트 일괄 정리 |
| `builder_sessions` | `user_id` | unspecified | **CASCADE** | 동일 |
| `agent_triggers` | `user_id` | unspecified | **CASCADE** | 동일 |
| `tools` | `user_id` | CASCADE | 유지 | OK |
| `credentials` | `user_id` | CASCADE | 유지 | OK |
| `daily_spend_users` | `user_id` | CASCADE | 유지 | OK |
| `refresh_tokens` | `user_id` | (신규) | **CASCADE** | 사용자 삭제 시 토큰 청소 |
| `oauth_accounts` | `user_id` | (Phase 2) | **CASCADE** | Phase 2 |

**주의**: LangGraph `PostgresSaver`의 `checkpoints*` 테이블은 `user_id` FK가 없다 → CASCADE 안 됨. `user_service.delete_user`가 conversation 별로 `checkpointer.delete_thread()`를 호출해야 한다 (Phase 6 참조).

### 4.6 마이그레이션 파일

- `backend/alembic/versions/m22_multiuser_auth.py` (신규).
- 순서: ① `users` 컬럼 추가 → ② `refresh_tokens` 신설 → ③ `tools.is_system` / `credentials.is_system` 추가 + CHECK → ④ `agents`/`builder_sessions`/`agent_triggers` FK drop+recreate (CASCADE) → ⑤ 데이터 백필 (mock user → super_user, `user_id IS NULL` tools/credentials → `is_system=TRUE`).
- `oauth_accounts`는 본 마이그레이션에서 **만들지 않는다**.

---

## 5. API Contract

### 5.1 엔드포인트 명세

#### POST `/api/auth/register`

| 항목 | 값 |
|------|----|
| 인증 | 없음 |
| Rate limit | IP당 5회/시간 |
| Request body | `{"email": "user@example.com", "password": "min8chars", "name": "Display Name"}` |
| Validation | email RFC5322, password ≥ 8자, name 1–80자 |
| 200/201 | `201 Created` |
| Response body | `{"user": {"id": UUID, "email": str, "name": str, "is_super_user": bool, "created_at": ISO8601}, "csrf_token": str}` |
| Set-Cookie (3) | `moldy_at` (HttpOnly, Secure*, SameSite=Lax, Path=/, Max-Age=3600), `moldy_rt` (HttpOnly, Secure*, SameSite=Lax, Path=/, Max-Age=2592000), `moldy_csrf` (Secure*, SameSite=Lax, Path=/, Max-Age=3600 — JS 읽기 가능, double-submit용) |
| 부수 효과 | DB에서 `users.count()=0`이면 `is_super_user=TRUE` 부여 후 생성. 자동 로그인 (토큰 3종 발급) |
| 에러 | `409 email_already_exists`, `422 validation_error`, `429 too_many_requests` |

\* `Secure`는 `settings.cookie_secure`에 따름 (dev=false, prod=true).

#### POST `/api/auth/login`

| 항목 | 값 |
|------|----|
| 인증 | 없음 |
| Rate limit | IP+email당 10회/분 |
| Request body | `{"email": str, "password": str}` |
| 200 | `{"user": {...}, "csrf_token": str}` + Set-Cookie 3종 |
| 부수 효과 | 성공 시 `last_login_at`, `last_login_ip` 갱신, `failed_login_attempts=0`. 실패 시 `failed_login_attempts++`, 5회 도달 시 `locked_until = NOW + 15min` |
| 에러 | `401 invalid_credentials`, `423 account_locked` (locked_until 미만료 시), `403 account_inactive` (is_active=false), `429 too_many_requests` |

#### POST `/api/auth/logout`

| 항목 | 값 |
|------|----|
| 인증 | required (access cookie) + CSRF |
| 200 | `{"ok": true}` |
| 부수 효과 | 현재 refresh token row의 `revoked_at = NOW()`. Clear-Cookie 3종 (Max-Age=0) |
| 에러 | `401 not_authenticated`, `403 csrf_mismatch` |

#### POST `/api/auth/refresh`

| 항목 | 값 |
|------|----|
| 인증 | refresh cookie (`moldy_rt`) 필수 |
| CSRF | **불필요** (cookie만으로 회전 — body 없음) |
| Rate limit | IP당 30회/분 |
| Request body | (없음) |
| 200 | `{"csrf_token": str}` + 새 Set-Cookie 3종 |
| 부수 효과 | 기존 refresh row revoke + 새 access/refresh/csrf 발급. Replay 감지 시 user의 모든 refresh 일괄 폐기 후 401 |
| 에러 | `401 invalid_refresh` (만료/revoked/replay) |

#### GET `/api/auth/me`

| 항목 | 값 |
|------|----|
| 인증 | required |
| CSRF | 불필요 (GET) |
| 200 | `{"user": {"id": UUID, "email": str, "name": str, "is_super_user": bool, "is_active": bool, "created_at": ISO8601, "last_login_at": ISO8601 \| null}}` |
| 에러 | `401 not_authenticated` |

### 5.2 에러 표준

| HTTP | 코드 | 의미 |
|------|------|------|
| 401 | `not_authenticated`, `invalid_credentials`, `invalid_refresh` | 토큰/자격 불일치 |
| 403 | `csrf_mismatch`, `account_inactive`, `forbidden` | 인가 실패 |
| 409 | `email_already_exists` | 중복 |
| 422 | `validation_error` | Pydantic 검증 실패 |
| 423 | `account_locked` | 5회 실패 후 잠금 (Retry-After 헤더 포함) |
| 429 | `too_many_requests` | rate limit |

응답 본문 형식 (모든 에러 공통):
```json
{ "detail": { "code": "invalid_credentials", "message": "Email or password is incorrect." } }
```

---

## 6. Authorization Pattern

### 6.1 의존성 시그니처

`backend/app/dependencies.py`:

```python
@dataclass(frozen=True)
class CurrentUser:
    id: UUID
    email: str
    name: str
    is_super_user: bool = False
    # 미래: workspace_id: UUID | None = None

async def get_current_user(
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> CurrentUser:
    """access cookie 또는 Authorization 헤더에서 토큰 추출 → JWT decode → User 조회.
    실패 시 401 not_authenticated. 비활성 user는 401."""

async def get_current_user_optional(
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> CurrentUser | None:
    """share endpoint 등 anonymous 허용용. 토큰 없거나 잘못되어도 None 반환."""

async def require_super_user(
    user: CurrentUser = Depends(get_current_user),
) -> CurrentUser:
    """is_super_user=False면 403 forbidden."""

async def verify_csrf(request: Request) -> None:
    """method가 GET/HEAD/OPTIONS이면 즉시 통과.
    그 외에는 X-CSRF-Token 헤더 == moldy_csrf cookie (double-submit) 검증.
    불일치 시 403 csrf_mismatch."""
```

### 6.2 라우터 적용 규칙

- 모든 도메인 라우터: `Depends(get_current_user)` + `Depends(verify_csrf)`(mutation에 한함).
- `templates`, `models`의 create/update/delete: `Depends(require_super_user)`로 교체.
- `health`, `/api/auth/login|register|refresh`, `shares/{token}/...`: 인증 의존성 없음.
- `verify_csrf`는 `/api/auth/refresh` (cookie 회전), `/api/auth/login|register` (CSRF 발급 전), `shares/{token}/...` (공개 link)에서 제외.

---

## 7. Workspace 확장 청사진

본 ADR은 **User 단위 테넌시**만 구현하지만, 미래 Workspace/Org 도입 시 마이그레이션 비용을 최소화하기 위해 다음 단계를 명시한다.

### 7.1 미래 마이그레이션 단계 6개

1. **`workspaces` 테이블 신설** — `(id, name, type ENUM('personal','team','division','company'), owner_user_id, parent_workspace_id NULL, created_at)`.
2. **모든 user에 personal workspace 자동 생성** — backfill script. `name = "{user.name}'s workspace"`, `type='personal'`, `owner_user_id=user.id`.
3. **`workspace_members` + 리소스 테이블에 `workspace_id` 컬럼 추가** — 모두 `NULL` 허용으로 시작.
4. **백필** — 각 row의 `user_id`로 personal workspace 조회 → `workspace_id` 채움 → NOT NULL 전환.
5. **권한 모델 확장** — `assert_can_read(resource, user, workspace)` 함수가 workspace membership + role을 검사. `is_super_user`는 global admin 의미로 유지.
6. **UI workspace switcher 추가** — 사이드바 상단에 workspace dropdown, switching 시 모든 query invalidate.

### 7.2 지금 할 것 (지금 ADR의 적용 범위)

- 새로 작성하는 service layer 함수는 **반드시 user 객체 (또는 `CurrentUser`)를 인자로 받도록 시그니처 통일**: `list_agents(db, user)`, `get_owned_agent(db, agent_id, user)`. 이렇게 두면 미래에 `owner=TenantContext(user, workspace)`로 단일 매개변수만 교체하면 된다.
- `user_id` 컬럼명은 **그대로 유지** (의미: "primary owner"). rename 비용을 피한다.
- 라우터에서 `user.id`를 직접 사용하는 패턴은 허용하되, 권한 검증 로직은 가능하면 service 레이어에 일원화.

지금 시점에서 새 테이블/컬럼은 **만들지 않는다**.

---

## 8. Consequences

### 8.1 Pros

- **격리 확보**: User A의 리소스를 User B가 접근할 수 없는 production-ready 격리.
- **비용 안전**: System credential super_user 전용 → 일반 사용자가 운영자 LLM 키로 호출 불가.
- **확장 hook**: service layer가 user 객체를 받는 시그니처로 통일 → Workspace 도입 시 시그니처만 확장.
- **표준 패턴**: HttpOnly cookie + CSRF + bcrypt + JWT — OWASP 권장에 부합.

### 8.2 Cons

- **신규 사용자 onboarding 복잡도 증가**: 본인 LLM credential 등록 안 하면 채팅 불가. 첫 가입 후 안내 모달/리다이렉트 UX 필수 (Phase 7에서 처리).
- **이메일 검증 미구현**: MVP에서는 가짜 이메일로도 가입 가능. Phase 2에서 SMTP + 검증 토큰 활성화 시까지 운영자가 신뢰 도메인만 허용하는 식으로 운용해야 함.
- **OAuth 부재**: Google/GitHub 로그인 없음. Phase 2까지 사용자는 비밀번호 관리 부담.
- **Mock user 제거에 따른 1회성 마이그레이션 비용**: 기존 PoC 데이터를 첫 super_user에 이전하는 스크립트 운영 필요.

### 8.3 Trade-offs

- **Redis 미사용** vs **DB whitelist**: Redis가 더 빠르지만 인프라 단순화 우선. `refresh_tokens` 인덱스로 충분한 성능.
- **단일 `is_super_user` 플래그** vs **RBAC 테이블**: MVP 단순화 우선. 향후 추가될 때 boolean → role enum 마이그레이션 비용은 한 번 발생.
- **CSRF double-submit** vs **synchronizer token**: double-submit이 stateless(서버 저장 불필요) → 운영 단순화.

### 8.4 Risks & Mitigations

| 리스크 | 심각도 | 완화책 |
|--------|--------|--------|
| Mock user → 신규 super_user 마이그레이션 중 데이터 유실 | 높음 | `backend/scripts/migrate_mock_to_real_user.py`를 트랜잭션 + dry-run 옵션으로 작성. staging에서 1회 검증 후 production 적용 |
| 첫 가입자가 외부 공격자에게 탈취되어 super_user 권한 획득 | **매우 높음** | (a) production 배포 시 운영자가 즉시 가입, (b) 가입 직후 `ALLOW_FIRST_USER_AS_ADMIN=false`로 변경, (c) 또는 production은 SQL 직접 부여로 우회. `.env.example`에 경고 명시 |
| Refresh token replay 공격 | 중간 | rotation 시 이전 token revoke. 이미 revoked인 token 재사용 감지 시 user의 모든 refresh 일괄 폐기 (force re-login) |
| 신규 사용자가 LLM credential 미등록 → 채팅 첫 시도 실패 → 이탈 | 중간 | 가입 후 onboarding 모달 + 첫 에이전트 생성 시 credential 미등록이면 등록 페이지로 redirect (Phase 7) |
| Cookie SameSite 설정 미스로 OAuth callback (Phase 2) 실패 | 낮음 (Phase 2 이슈) | dev=lax, prod에서도 same-origin이면 lax OK. Phase 2 OAuth 추가 시 partitioned cookie 검토 |
| LangGraph checkpoint를 다른 user가 thread_id 추측해서 접근 | 낮음 (UUID v4) | conversation 소유권 검증을 router 레벨에서 유지 (이미 구현됨). 추후 thread_id에 user_id prefix 부여 검토 |
| 5회 실패 잠금이 brute-force가 아닌 사용자 비밀번호 망각으로 인한 false positive | 낮음 | `locked_until = NOW + 15min`으로 영구 잠금 회피. Phase 2에서 비밀번호 재설정 활성화로 근본 해결 |

---

## Appendix A — 검증 체크리스트

- [ ] `uv run alembic upgrade head` → `users` 신규 컬럼, `refresh_tokens` 테이블, `tools.is_system`, `credentials.is_system` 모두 존재.
- [ ] `uv run alembic downgrade -1 && uv run alembic upgrade head` 성공 (idempotent).
- [ ] 첫 가입자 `is_super_user=TRUE`, 두 번째 가입자 `is_super_user=FALSE` (DB 직접 확인).
- [ ] User A 로그인 후 User B 리소스 조회 → 모두 404.
- [ ] 일반 user의 LLM 호출이 본인 credential 미등록 시 명확한 에러 메시지 ("Add your own API key…").
- [ ] super_user는 system credential 조회/관리 가능. 일반 user는 거부.
- [ ] Refresh token replay → 모든 토큰 폐기 + 401.
- [ ] Production: `cookie_secure=true`, `cors_allowed_origins` 운영 도메인 제한, `JWT_SECRET` ≥ 32바이트 랜덤.
