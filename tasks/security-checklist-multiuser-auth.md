# Security Checklist — ADR-016 Multi-user Auth

검증 일자: 2026-05-09
검증자: bezos (S7 통합 검증)
브랜치: `feature/multiuser-auth`

---

## 1. Cookie 보안

| 항목 | 상태 | 메모 |
|------|------|------|
| Access cookie `httponly=True` | PASS | `app/auth/cookies.py:52` |
| Refresh cookie `httponly=True` | PASS | `app/auth/cookies.py:57` |
| CSRF cookie `httponly=False` | PASS | double-submit 패턴, JS read 필요 — 의도된 설정 |
| `cookie_secure` config 토글 | PASS | dev=False (HTTP), prod=True 운영자가 반드시 설정 (`config.py:120`) |
| `cookie_samesite` 기본 `lax` | PASS | cross-origin POST 차단 |
| Cookie max_age == JWT exp | PASS | 좀비 쿠키 방지 (`cookies.py:52`) |

**운영 액션 필수**: `.env`에 `COOKIE_SECURE=true` + 적절한 `COOKIE_DOMAIN` 설정. 미설정 시 HTTPS 환경에서도 `Secure` flag가 누락된다.

---

## 2. CORS

| 항목 | 상태 | 메모 |
|------|------|------|
| `allow_origins`는 wildcard 아님 | PASS | `main.py:226` 명시적 도메인 (`localhost:3000`) |
| `allow_credentials=True` | PASS | cookie flow 필수 |
| `expose_headers`는 최소화 | PASS | `X-Run-Id`, `X-Resume-Mode`만 |

**운영 액션 필수**: 프로덕션 배포 시 `main.py:226`의 hard-coded origins를 `settings.cors_allow_origins` 환경변수 기반으로 교체 — 현재는 dev origin이 박혀 있어 프로덕션에서 도메인이 다르면 작동 불가 (또는 새 코드 PR 필요).

---

## 3. JWT

| 항목 | 상태 | 메모 |
|------|------|------|
| `JWT_SECRET` 환경변수 분리 | PASS | `config.py:110`, 코드 하드코딩 X |
| Dev fallback (ephemeral) 경고 로그 | PASS | `auth/jwt.py:58-64` WARNING 로그 |
| HS256 알고리즘 | PASS | 단일 백엔드 → 대칭키 OK |
| 토큰 type 분리 (`access`/`refresh`/`csrf`) | PASS | decode 시 `expected_type` 검증 |
| Refresh 토큰 SHA-256 hash 저장 | PASS | DB leak 시 토큰 자체 노출 X |
| Required claims (`exp`, `iat`, `sub`, `type`, `jti`) | PASS | `decode_token` `options.require` |

**운영 액션 필수**: 프로덕션 deploy 전 `JWT_SECRET`을 32 byte 이상 랜덤 값으로 환경변수 설정 (`openssl rand -base64 48`). 미설정 시 매 프로세스 재시작마다 모든 세션이 무효화된다.

---

## 4. Rate Limiting

| 항목 | 상태 | 메모 |
|------|------|------|
| `/register` rate limit | PASS | `5/hour` (`routers/auth.py:53`) |
| `/login` rate limit | PASS | `10/minute` (`routers/auth.py:81`) |
| `/refresh` rate limit | PASS | `30/minute` (`routers/auth.py:128`) |
| `/logout` rate limit | NONE | 로그아웃 abuse 표면 작음 — 허용 |
| Public share endpoint | PASS | `60/minute` per IP |

**운영 권고**: `slowapi`의 in-memory storage는 다중 워커 환경에서 워커별로 카운트가 갈라진다. 프로덕션은 Redis 백엔드로 교체 권고.

---

## 5. Password / 계정 보호

| 항목 | 상태 | 메모 |
|------|------|------|
| bcrypt cost factor 12 | PASS | `auth/password.py:31` (OWASP 2023 권고) |
| `passlib` CryptContext 사용 | PASS | 알고리즘 swap 가능 구조 |
| Timing attack 완화 (dummy verify) | PASS | unknown email에서도 verify 호출 (`auth_service.py:107`) |
| Min password length 8 | PASS | `schemas/auth.py:15` |
| Failed login counter | PARTIAL | **BUG**: 카운터 증가가 commit되지 않음 (escalation 1) |
| Account lockout (5회 → 15분) | PARTIAL | **BUG**: 동일 root cause로 lockout 미작동 (escalation 1) |

---

## 6. Refresh Token 보안

| 항목 | 상태 | 메모 |
|------|------|------|
| Refresh rotation on each use | PASS | 정상 경로 검증 (test_auth_refresh) |
| Replay 401 응답 | PASS | 즉시 거부 |
| Replay 시 mass-revoke 활성 토큰 | PARTIAL | **BUG**: AppError 전 revoke가 commit되지 않음 (escalation 2) |
| DB whitelist 검증 (`token_hash`) | PASS | unique index, scalar 조회 |
| Expiry 검증 (`expires_at <= now`) | PASS | 401 invalid_refresh |

---

## 7. CSRF

| 항목 | 상태 | 메모 |
|------|------|------|
| Double-submit (header == cookie) | PASS | 7/7 테스트 통과 |
| Bootstrap endpoints exempt | PASS | register/login/refresh — 사용자 미존재 |
| GET/HEAD/OPTIONS exempt | PASS | safe methods |
| Cross-account CSRF token 거부 | PASS | `sub` claim user.id 비교 |
| Garbage token 거부 | PASS | InvalidTokenError → 403 |

---

## 8. 권한 분리 (RBAC)

| 항목 | 상태 | 메모 |
|------|------|------|
| `require_super_user` gate | PASS | 6개 엔드포인트 (system-credentials × 5, models POST/PATCH/DELETE × 3) |
| 일반 user의 system credential 접근 | PASS | 403 (테스트 검증) |
| 일반 user의 model mutation | PASS | 403 (테스트 검증) |
| First user 자동 super_user | PASS | `allow_first_user_as_admin` 토글 |

**운영 액션 필수**: 운영자 계정 가입 직후 `.env`에서 `ALLOW_FIRST_USER_AS_ADMIN=false`로 변경. 미변경 시 사고로 DB가 비면 다음 가입자가 super_user가 됨.

---

## 9. Multi-user Isolation Matrix (10/10 PASS)

| 시나리오 | 결과 | 응답 |
|----------|------|------|
| User B → User A's agent GET | PASS | 404 (not 403, enumeration oracle 차단) |
| User B → User A's agent PUT/DELETE | PASS | 404 |
| Agent list per-user filter | PASS | B는 빈 리스트 |
| User B → User A's trigger PUT/DELETE | PASS | 404 |
| 일반 user → /api/system-credentials GET | PASS | 403 |
| 일반 user → /api/system-credentials POST | PASS | 403 |
| Super_user → /api/system-credentials GET | PASS | 200 |
| 일반 user → /api/models POST | PASS | 403 |
| User B → User A's agent usage | PASS | 404 또는 빈 aggregate |
| User B → User A's conversation PATCH/DELETE | PASS | 404 |

---

## 10. User Deletion / Cleanup (8/8 PASS)

- LangGraph thread 삭제 per conversation: PASS
- 다른 user의 thread는 보존: PASS
- Active refresh tokens revoke: PASS
- Checkpointer unavailable 시에도 refresh revoke: PASS
- User row 삭제 → agent CASCADE: PASS
- System credential (`user_id=NULL`) 보존: PASS
- RefreshToken FK CASCADE: PASS
- Unknown user_id no-op: PASS

---

## OWASP Top 10 자가점검

| 카테고리 | 결과 | 비고 |
|----------|------|------|
| A01 Broken Access Control | PASS | super_user 가드 + service-level owner filter |
| A02 Cryptographic Failures | PASS | bcrypt cost 12, JWT HS256, refresh token hash storage |
| A03 Injection | PASS | SQLAlchemy ORM (raw SQL 없음) |
| A04 Insecure Design | NOTE | Audit log은 `actor_user_id`만 — 행위 자체의 immutable trail은 없음 (Phase 2 후속) |
| A05 Security Misconfiguration | RISK | `cookie_secure`/`cors_allow_origins` 운영자 설정 의존 (위 섹션 1, 2 운영 액션) |
| A06 Vulnerable Components | DEFER | dependency scan은 별도 CI 이슈 |
| A07 Auth Failures | **PARTIAL** | replay defense + lockout 둘 다 commit 누락 버그 (escalation 1, 2) |
| A08 Software Integrity Failures | PASS | JWT signature 검증, CHECK constraint |
| A09 Logging Failures | PASS | `logger.warning` on replay, `actor_user_id` audit |
| A10 SSRF | N/A | 사용자가 server-side URL fetch를 직접 트리거하는 경로 미식별 |

---

## ESCALATION 사항 (사티아 → 코드 owner에게)

### Escalation 1 — Login failure 카운터 미커밋 (CRITICAL)

**증상**: `auth_service.authenticate`에서 비밀번호 오류 시 `record_login_failure(db, user)` 호출 후 즉시 `AppError` raise. 라우터는 success path에서만 `db.commit()`을 호출하므로 카운터 증가가 롤백된다.

**영향**: `failed_login_attempts`가 영구히 0 → 5회 lockout이 실제로 작동하지 않음. 무제한 brute-force 가능.

**수정 위치**: `backend/app/routers/auth.py` `login_endpoint` 또는 `services/user_service.py` `record_login_failure`.

**권고 fix**: `record_login_failure`를 별도 short-lived session에서 실행하거나, 라우터에서 `try/except AppError`로 감싸서 commit 후 re-raise.

**테스트**: `tests/test_auth_login.py::test_wrong_password_returns_401_and_increments_counter` (xfail strict). 수정 후 `xfail` 데코레이터 제거.

### Escalation 2 — Refresh replay mass-revoke 미커밋 (CRITICAL)

**증상**: `auth_service.rotate_refresh`에서 replay 감지 시 `_revoke_all_active(db, user_id)` UPDATE 발행 후 `AppError` raise. 라우터 commit 누락으로 mass-revoke 롤백.

**영향**: 도난당한 refresh token이 노출되어도 victim의 active 세션이 자동 무효화되지 않음. ADR-016 §5.2의 핵심 보안 결정이 무력화됨.

**수정 위치**: 동일 (`auth_service.rotate_refresh` 내부에서 `await db.commit()` 후 raise, 또는 라우터에서 처리).

**테스트**: `tests/test_auth_refresh.py::test_refresh_replay_revokes_all_active` (xfail strict).

---

## 운영자 deploy 직전 필수 액션 3가지

1. **`JWT_SECRET`을 32 byte 이상 랜덤 값으로 환경변수에 설정.** 미설정 시 ephemeral 키로 토큰이 매 재시작마다 무효화된다.
2. **`COOKIE_SECURE=true`로 변경 + `COOKIE_DOMAIN` 명시.** HTTPS 환경에서도 미설정 시 cookie의 `Secure` flag가 누락되어 MITM 노출.
3. **위 2건의 ESCALATION 수정 머지 + `xfail` 데코레이터 제거 후 회귀 테스트 통과 확인.** 미수정 상태로 deploy하면 brute-force/refresh 도난 방어가 무력화된다.

추가 권고:
- 첫 운영자 가입 직후 `.env`의 `ALLOW_FIRST_USER_AS_ADMIN=false`로 토글 끄기.
- `main.py:226`의 `allow_origins`를 환경변수 기반으로 변경 (현재 dev origin 하드코딩).
- `slowapi` rate-limit storage를 Redis로 교체 (다중 워커 환경).
