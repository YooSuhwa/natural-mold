# Operator Setup — 운영 환경 셋업 체크리스트

운영 배포 전 반드시 통과해야 하는 환경 변수 셋업입니다. `APP_ENV=production`
으로 부팅하면 서버가 직접 검증하여 문제가 하나라도 있으면 **부팅을 거부**합니다
(`app/security/production_check.py` 참조).

ADR-016 §8.4 / HANDOFF #2.

---

## 1. 필수 환경 변수

| 변수 | 운영 값 | 생성 명령 |
|------|---------|-----------|
| `APP_ENV` | `production` | — |
| `JWT_SECRET` | 32+ 자 랜덤 문자열 | `python -c "import secrets; print(secrets.token_urlsafe(48))"` |
| `COOKIE_SECURE` | `true` | — (HTTPS 필수) |
| `ALLOW_FIRST_USER_AS_ADMIN` | `false` | 운영자 계정 생성 후 즉시 |
| `CORS_ALLOWED_ORIGINS` | 실제 프론트 origin 콤마 구분 | 예: `https://moldy.example.com,https://staging.moldy.example.com` |
| `ENCRYPTION_KEYS` | 64자 hex 키 (콤마 구분) | `python -c "import secrets; print(secrets.token_hex(32))"` |

선택:
- `COOKIE_DOMAIN` — 서브도메인 공유 시 (`.moldy.example.com`)
- `COOKIE_SAMESITE` — cross-site fetch 필요 시 `none` (단 `COOKIE_SECURE=true` 강제)

---

## 2. 운영 검증

배포 직전에 동일 검증을 수동 실행하려면:

```bash
APP_ENV=production python -c "
from app.config import settings
from app.security.production_check import enforce_production_safety
enforce_production_safety(settings)
print('OK — production settings clean')
"
```

문제가 있으면 어떤 변수가 왜 잘못됐는지 액션 가능한 메시지로 출력합니다.

---

## 3. 부팅 거부 예시

`APP_ENV=production` 인데 `COOKIE_SECURE=false`로 부팅 시도:

```
RuntimeError: Refusing to start with insecure production settings:
  - COOKIE_SECURE=false. Set true so browsers refuse to send auth
    cookies over plain HTTP.
Fix the above and restart, or set APP_ENV=dev to bypass
(local development only).
```

---

## 4. 첫 운영자 계정 부트스트랩

`ALLOW_FIRST_USER_AS_ADMIN=true`가 켜진 상태로만 자동 승격이 동작합니다.
운영자 계정 생성 절차:

1. 일시적으로 `ALLOW_FIRST_USER_AS_ADMIN=true` + `APP_ENV=dev`로 부팅
2. `/api/auth/register`로 운영자 계정 1건 생성 — 자동으로 `is_super_user=true`
3. 즉시 `ALLOW_FIRST_USER_AS_ADMIN=false` + `APP_ENV=production`으로 재부팅
4. 이후 등록되는 모든 user는 평민. super_user 부여는 운영자가 DB / 어드민 API로 수동

---

## 5. 마이그레이션

```bash
cd backend
uv run alembic upgrade head
```

(스키마 변경이 있는 PR을 머지한 직후 항상 실행)

---

## 6. 관련 문서

- `docs/design-docs/adr-016-multiuser-auth.md` — 인증/세션 전체 설계
- `backend/.env.example` — 전체 변수 목록 + 인라인 주석
- `backend/app/security/production_check.py` — 검증 로직 (단일 소스)
