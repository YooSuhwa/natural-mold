# HANDOFF — Multi-User Authentication

**Branch**: `feature/multiuser-auth` (from `main` 52fd954)
**Date**: 2026-05-09
**Plan source**: `~/.claude/plans/replicated-crunching-lark.md` (approved)
**ADR**: `docs/design-docs/adr-016-multiuser-auth.md`
**Status**: ✅ CONDITIONAL GO — see "운영자 필수 액션" before production deploy.

---

## 1. 변경 사항 요약

### 백엔드 (FastAPI + SQLAlchemy + Alembic)
- 인증 코어 신규: `app/auth/{password,jwt,cookies}.py` + `app/services/{user_service,auth_service}.py` + `app/schemas/auth.py` + `app/routers/auth.py`
- `app/dependencies.py` 재작성 — JWT 기반 `get_current_user`, `get_current_user_optional`, `require_super_user`, `verify_csrf`
- `app/models/user.py` 12개 컬럼 추가 (hashed_password, is_active, is_super_user, last_login_at, last_login_ip, failed_login_attempts, locked_until, email_verified_at + 5개 Phase 2용)
- `app/models/refresh_token.py` 신규 — token_hash + revoked_at + replay 감지
- `app/models/{tool,credential}.py` `is_system` BOOLEAN + CHECK constraint(`is_system=FALSE OR user_id IS NULL`)
- `app/models/{agent,builder_session,agent_trigger}.py` FK ON DELETE CASCADE 명시
- `alembic/versions/m36_multiuser_auth.py` (round-trip 통과)
- `app/main.py` startup mock user 자동 생성 블록 **제거**
- `app/seed/bootstrap_from_env.py` → `bootstrap_system_credentials(db)` (`is_system=True, user_id=NULL`로 저장)
- `app/services/credential_service.py` super_user 분기 — 일반 user는 system credential 차단
- `app/agent_runtime/tool_factory.py` 일반 user agent가 system credential 의존 시 명확한 에러
- `app/agent_runtime/executor.py` `AgentConfig.__post_init__`에 user_id 가드
- 6개 엔드포인트에 `require_super_user` (credentials system 5 + models mutation 3)
- 63/66 mutation endpoint에 `verify_csrf` (auth/login·register·refresh 제외)
- `routers/conversations.py` update/delete/switch_branch에 owner 검증 보강 (S3 추가 발견)
- CORS env-driven 분리 (`cors_allowed_origins`, `allow_methods`/`allow_headers` 명시)

### 프론트엔드 (Next.js 16 App Router)
- `proxy.ts` 신규 (Next 16 컨벤션 — middleware.ts 대체) — 보호 라우트 ↔ /login 양방향 redirect, callbackUrl 보존
- `(auth)` route group + `login`/`register` 페이지
- `lib/api/client.ts` 전면 개편 — `credentials:'include'` + 자동 X-CSRF-Token + 401 dedup refresh + 만료 시 toast/redirect
- `lib/auth/{csrf,session}.ts`, `lib/hooks/useAuth.ts`, `lib/api/auth.ts`
- `components/auth/*` 7종 (LoginForm, RegisterForm, UserMenu, AuthGuard, OnboardingDialog, PasswordStrengthMeter, AuthAlert)
- 사이드바/헤더 mock user 제거 → `useSession()` + `<UserMenu />`
- 한국어 i18n 메시지 추가

### 도구/스크립트/문서
- `scripts/migrate_mock_to_real_user.py` 신규 (idempotent, dry-run, --delete-source)
- 통합 테스트 5종 신규 + 6 보강 = **950 백엔드 테스트 PASS / 0 FAIL / 0 xfail**
- `docs/design-docs/adr-016-multiuser-auth.md` (370줄)
- `docs/design-docs/multiuser-auth-ui-spec.md` (664줄)
- `docs/ARCHITECTURE.md` Authentication 섹션 추가
- `tasks/deletion-analysis-multiuser-auth.md`, `tasks/security-checklist-multiuser-auth.md`, `tasks/lessons.md` 갱신
- `docs/QUALITY_SCORE.md` Authentication 도메인 추가

**총 변경**: 75 파일 (49 modified + 26 untracked, 미커밋).

---

## 2. 핵심 아키텍처 결정

| 항목 | 선택 | 근거 |
|------|------|------|
| 토큰 저장 | HttpOnly Cookie + double-submit CSRF | XSS 내성. plan의 "body only"는 ADR이 double-submit으로 override (S2 결정) |
| JWT | HS256, access 1h, refresh 30d (DB whitelist + replay 감지) | 재사용 감지 시 user 전체 active 폐기 |
| 비밀번호 | bcrypt (passlib) | passlib 1.7.4 호환 위해 bcrypt<5 핀 + `__about__` 셰임 (passlib 1.8 후 제거) |
| 권한 모델 | `is_super_user` boolean 단일 플래그 | MVP — 향후 RBAC 확장 가능 |
| 테넌시 | User-only + Workspace 확장 hook (service layer 시그니처 user 객체 통일) | 미래 마이그레이션 5단계 청사진 ADR-016 §6 |
| 인증 방식 | Email + Password 단독 | OAuth는 Phase 2 |
| System credentials | super_user 전용 (조회/사용/관리 모두) | 비용 폭주 방지 + 보안 |
| 첫 가입자 | 자동 super_user (`ALLOW_FIRST_USER_AS_ADMIN=true`) | 운영자 가입 직후 false 토글 필수 |
| Alembic 번호 | m36 (ADR이 m22로 명시했으나 실제 m22-m35 점유) | ADR 사후 보정 — lessons.md 기록 |

---

## 3. 삭제된 항목 (Musk Step 2)
- `mock_user_id`/`mock_user_email`/`mock_user_name` 3개 settings 필드
- `app/main.py` startup mock user 자동 생성 블록 (~30 LOC)
- `bootstrap_credentials_from_env(db, user_id)` → `bootstrap_system_credentials(db)` (인자 단순화)
- `.env.example` Mock user 섹션
- `agent_creation_sessions` dead table 식별 (S1 발견 — 코드 참조 0건. 마이그레이션은 별도 PR로 분리)

---

## 4. TTH Ralph Loop 통계

| 사일로 | 담당 | 1회 통과 | 비고 |
|--------|------|---------|------|
| S0 ADR-016 | 피차이 (Opus) | ✅ | 370줄 |
| S1 삭제 분석 | 베조스 (Opus) | ✅ | 보안 critical 3건 식별 |
| S2 백엔드 코어 | 젠슨 (Opus) | ✅ | 19파일, m36 round-trip OK |
| S3 시드 + audit | 젠슨 (Opus) | ✅ | mock 흔적 0건 |
| S4 UI 스펙 | 팀쿡 (Opus) | ✅ | 664줄, 컴포넌트 7종 |
| S5 프론트엔드 | 저커버그 (Opus) | ✅ | 24 파일, build/lint PASS |
| S6 AI runtime + 마이그 | 젠슨 (Opus) | ✅ | google_tools 이미 분산됨 발견 |
| S7 통합 검증 | 베조스 (Opus) | ✅ | 947 PASS, **CRITICAL 2건 ESCALATION** |
| ESCALATION fix | 젠슨 (Opus) | ✅ | 트랜잭션 경계 commit 누락 → 950 PASS |

**1회 통과**: 9/9. **ESCALATION**: 1건 (S7 → 후속 fix). **사일로 충돌**: 0건.

---

## 5. 운영자 필수 액션 (Production Deploy 전)

### 1️⃣ JWT/Cookie/CORS 환경변수 설정
```bash
JWT_SECRET=$(python -c "import secrets; print(secrets.token_urlsafe(64))")
COOKIE_SECURE=true
COOKIE_DOMAIN=.moldy.dev
CORS_ALLOWED_ORIGINS=https://moldy.dev,https://www.moldy.dev
```
**미설정 시**: ephemeral JWT (재시작마다 토큰 무효), Secure flag 누락(HTTP cookie 노출), CORS wildcard 차단됨.

### 2️⃣ 첫 운영자 가입 직후 super_user 토글 OFF
```bash
ALLOW_FIRST_USER_AS_ADMIN=false
```
**위험**: DB 사고로 user 테이블 비워지면 다음 가입자가 super_user 자동 획득.

### 3️⃣ Mock User 데이터 마이그레이션
```bash
# 1. 운영자 회원가입(첫 가입자 → super_user 자동)
# 2. dry-run으로 변경 사항 확인
cd backend
uv run python scripts/migrate_mock_to_real_user.py --target-user-id <운영자 UUID> --dry-run
# 3. 실제 실행
uv run python scripts/migrate_mock_to_real_user.py --target-user-id <운영자 UUID> --delete-source
```
6 테이블 user_id 이전, 단일 트랜잭션, idempotent. system credential은 user_id=NULL 유지.

---

## 6. 검증 결과

```
백엔드: 950 PASS / 0 FAIL / 0 xfail
        ruff clean
        alembic round-trip OK (upgrade → downgrade -1 → upgrade head)
        mock_user grep 0건

프론트엔드: pnpm build PASS
            pnpm lint PASS

격리 매트릭스: 10/10 시나리오 통과 (enumeration oracle 차단)
보안 체크리스트: 8/10 (2건은 운영 설정 의존 — §5)
```

---

## 7. 알려진 한계 / 후속 작업

### Phase 2 (별도 PR)
- [ ] Google OAuth 로그인 (지금은 도구용 OAuth만 동작)
- [ ] 이메일 검증 (token 컬럼은 추가됨, SMTP/UX 미구현)
- [ ] 비밀번호 재설정 (token 컬럼은 추가됨, 플로우 미구현)
- [ ] OAuthAccount 테이블 (ADR 명시, 마이그 미실행)

### Phase 3 (Workspace 확장 — ADR-016 §6 청사진)
1. Workspace 테이블 신설 (personal/team/division/company)
2. 모든 user에 personal workspace 자동 생성
3. 리소스에 `workspace_id` nullable 컬럼 + 백필
4. Service layer `TenantContext(user, workspace)` 점진 전환
5. UI workspace switcher

### 사소한 항목
- `agent_creation_sessions` dead table 제거 (별도 PR)
- `bcrypt<5` 핀 + passlib `__about__` 셰임 — passlib 1.8 릴리스 후 양쪽 제거
- M36 downgrade는 `credentials.user_id NOT NULL` 복원 시도 안 함 (system credential NULL이면 데이터 유실 — 주석 명시)
- LangGraph checkpoint thread_id에 user_id prefix 검토 (현재는 라우터 owner 검증으로 격리)

---

## 8. 배운 점

1. **CHECK constraint로 invariant DB 레벨 강제**: `is_system=FALSE OR user_id IS NULL` — 코드 버그를 ORM이 아닌 DB가 차단.
2. **Refresh replay = user 전체 폐기**: 단순 revoke만으로는 부족. 재사용 감지 시 mass-revoke로 force re-login.
3. **트랜잭션 경계 + commit 강제**: 카운터 갱신/대량 revoke 후 raise 전 `await db.commit()`. FastAPI는 raise 시 자동 rollback (ESCALATION 2건의 공통 원인).
4. **ADR vs 코드 번호 충돌 (m22 → m36)**: ADR이 코드 현실보다 앞설 수 있음. 코드 진실 우선 + ADR 사후 주석.
5. **Next 16 컨벤션 변경**: middleware.ts → proxy.ts. 기존 가이드는 더 이상 유효하지 않음.
6. **CSRF double-submit > body-only**: 클라이언트 cookie 폴백을 가지면 StrictMode/탭 갱신에서 더 견고.
7. **격리 oracle 통일**: 404 vs 403 차이가 enumeration leak. `get_owned_*` 패턴으로 응답 통일.

---

## 9. 다음 세션을 위한 컨텍스트

```
브랜치: feature/multiuser-auth (75 파일 미커밋)
ADR: docs/design-docs/adr-016-multiuser-auth.md
검증: cd backend && uv run pytest -v && cd ../frontend && pnpm build && pnpm lint
첫 작업 추천: 위 §5의 운영자 필수 액션 3건 검토 + git commit + PR 생성
```

ESCALATION 모두 해소 + 950 PASS 후 **GO** 판정. PR 생성 → 머지 직전에 §5 환경변수 설정 + mock user 데이터 이전 스크립트 실행 필수.
