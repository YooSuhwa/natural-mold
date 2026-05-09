# Multi-User Auth — 코드 리뷰 결과

**대상**: `feature/multiuser-auth` (uncommitted, 76 files vs main 52fd954)
**범위**: ADR-016 backend/frontend/scripts/tests
**검토자**: Claude Code (senior reviewer agent)
**날짜**: 2026-05-08

---

## 강점

1. **트랜잭션 경계가 ADR-016 §5와 정확히 일치**. `auth_service.authenticate`의 실패 카운터 commit (line 131), `rotate_refresh`의 replay mass-revoke commit (line 220) 둘 다 코멘트로 의도가 명시되어 있고 ESCALATION 후속 fix가 정확히 반영되어 있다.

2. **격리 oracle 일관성**. `credential_service.list_for_user`/`get_for_user`가 `is_system=False`를 강제하고, 라우터의 `_load_owned`는 ownership/존재 양쪽 모두 동일한 404를 반환 (line 105). enumeration oracle 방지.

3. **DB invariant이 m36에서 CHECK constraint으로 인코딩**. `(is_system=false) OR (user_id IS NULL)` — 애플리케이션 버그가 발생해도 DB가 차단. credential service의 `create()`에도 동일 invariant이 ValueError로 사전 검증.

4. **Refresh token 보안 모델이 견고**. SHA-256 hash만 DB 저장(line 31), partial index `WHERE revoked_at IS NULL` (m36 line 199), replay 감지 → mass-revoke까지 전부 ADR-016 §4.2와 매칭.

5. **CSRF double-submit + sub 일치 검증** (`dependencies.py:142-165`). 단순 header==cookie 비교를 넘어 JWT type=csrf 검증 + sub=user.id 일치까지 확인 — forwarded cookie 공격 차단.

6. **테스트 커버리지가 격리 매트릭스를 망라**. login(5종), refresh(6종 — replay/expired/unknown 포함), CSRF(7종 — wrong subject 포함), multiuser_isolation 311줄, user_cleanup 267줄. 회귀 가드로 충분.

---

## Critical 이슈

**없음.** ESCALATION 2건의 후속 fix가 정확히 반영되어 있으며, 같은 클래스의 잠재 버그(다른 함수의 트랜잭션 경계 누락)는 발견되지 않음.

회귀 검토:
- `register` → `register_endpoint`에서 commit 1회 (router line 70). 실패 시 rollback 정상.
- `revoke_refresh` → flush only (service line 265), router commit (auth.py:122). 정상.
- `_revoke_all_active` → 호출자 `rotate_refresh`가 commit 책임. 정상.
- `record_login_success` → flush only, router commit. 정상.

---

## High 이슈

### H1. CSRF 헤더 비교가 비-상수시간 (정보 누출 위험은 낮음)
- 파일: `backend/app/dependencies.py:158`
- 문제: `header != cookie` 직접 비교. 두 값 모두 attacker-controlled JWT이고 진짜 게이트는 JWT signature 검증이라 실질적 oracle은 없음. 그러나 보안 코드 컨벤션상 `secrets.compare_digest`가 권장.
- 제안:
  ```python
  import secrets
  if not header or not cookie or not secrets.compare_digest(header, cookie):
      raise AppError(code="csrf_mismatch", ...)
  ```
- 우선순위: 머지 후 follow-up으로 충분.

### H2. OAuth2 callback이 CSRF 검증 없이 cookie에서 user_id 결정
- 파일: `backend/app/routers/credentials.py:552-602`
- 문제: `oauth2_callback`은 `_OAUTH_STATE`의 `user_id`를 신뢰하여 credential 업데이트. state 토큰이 32바이트 random이라 추측 불가하고 외부 IdP가 state를 echo 하므로 실질적으로 안전. 다만 `actor_user_id=cred.user_id`로 audit 작성(line 596) — state가 변조됐을 때(짧은 가능성) 잘못된 user의 credential을 작성할 수 있음.
- 제안: state 검증 후 `pending["user_id"]`와 `cred.user_id` 일치 여부 추가 검증.
- 우선순위: PoC-grade라 명시되어 있으므로 머지 OK, 운영 마이그레이션 전 재확인.

### H3. `templates.py`에 인증 가드 없음
- 파일: `backend/app/routers/templates.py:17-29`
- 문제: 모든 템플릿이 unauthenticated. 시드 데이터라 의도적이라면 OK이지만 ADR-016 §6.1 — "기존 라우터는 그대로 컴파일"의 unintended consequence일 수도.
- 확인 필요: 의도적 공개라면 ADR/HANDOFF에 명시. 비의도적이면 `get_current_user` 추가.

### H4. 레지스터 라우터에서 register 실패 시 commit 누락 (이론상 무해)
- 파일: `backend/app/routers/auth.py:67-70`
- 문제: `auth_service.register`는 user create 후 flush만 하고 raise 하지 않으면 router의 `db.commit()` (line 70)이 처리. 단, `register` 자체가 `email_already_exists`를 raise한 경우 commit이 호출되지 않아 트랜잭션이 그대로 dangling — FastAPI 의존성이 generator finalize에서 rollback하므로 실질적으로는 safe. 명시적 commit/rollback이 더 견고.
- 제안: register service 자체를 flush만 하고 commit은 router에서 — 현재 그대로라 OK. 변경 불필요.

---

## Minor 개선

### M1. `_resolve_secret`의 ephemeral key가 module-level 캐시
- 파일: `backend/app/auth/jwt.py:51-67`
- 문제: 함수 attribute로 캐시. 멀티프로세스 (gunicorn workers) 환경에서는 worker마다 다른 ephemeral key — 사용자가 worker A에서 로그인 후 worker B로 라우팅되면 401. 이미 WARNING 로그가 있으므로 이슈는 알려져 있음.
- 제안: WARNING 로그에 "do not run multiple workers without JWT_SECRET" 추가.

### M2. `RefreshToken.created_at`과 `issued_at` 중복
- 파일: `backend/app/models/refresh_token.py:34, 50`
- 문제: 두 컬럼 모두 `now()` server_default. 의도가 다르지 않으면 중복.
- 제안: 한 쪽 제거 또는 다른 의미 명시 (예: `created_at`은 row 생성, `issued_at`은 token 발급 — rotate 시 갱신되는지 명확화).

### M3. `cleanup_user_resources`의 datetime이 naive
- 파일: `backend/app/services/user_service.py:166`
- 문제: `datetime.now(UTC).replace(tzinfo=None)` — DB는 `DateTime(timezone=True)`. SQLAlchemy가 자동 변환하지만 다른 곳(`auth_service.py:179`)은 tz-aware. 일관성 위해 tz-aware 통일.

### M4. Frontend `proxy.ts`가 cookie 존재만 검증
- 파일: `frontend/src/proxy.ts:24`
- 문제: `moldy_rt` cookie의 존재 여부만 본다. 만료된 cookie도 redirect 통과 — `/login`까지 가서 `/me` 401로 다시 redirect되는 친화적 UX는 아님. 다만 brower가 만료 cookie는 보내지 않으므로 실제 영향 미미.
- 코멘트로 의도 명시 (line 17 "Cookie-based gate")로 충분.

### M5. `client.ts`의 `sessionExpiredFired` 1초 reset이 race 가능
- 파일: `frontend/src/lib/api/client.ts:81-83`
- 문제: 1초 후 `sessionExpiredFired = false`로 리셋. 그 사이 동시 다발 요청이 401 → refresh 실패 → 동일 핸들러를 또 호출하지 않으므로 OK. 다만 setTimeout 의존이 fragile.
- 제안: 핸들러 내부에서 unmount/네비게이션 완료 후 명시적으로 리셋하는 콜백 패턴이 더 견고.

### M6. `LoginForm`/`RegisterForm` 입력 길이 제한이 backend Pydantic과 부분 일치
- 파일: `frontend/src/components/auth/RegisterForm.tsx:80` (`maxLength={80}`) vs `backend/app/schemas/auth.py:16` (`max_length=100`)
- 문제: name 80 vs 100 — 위협은 아니지만 frontend가 더 엄격해 사용자가 100자까지 쓸 수 없음. 백엔드 100으로 통일 권장.

### M7. Migration script `--delete-source` 후 LangGraph checkpoint 정리 누락
- 파일: `backend/scripts/migrate_mock_to_real_user.py:138-144`
- 문제: source user를 raw SQL DELETE — `cleanup_user_resources` 호출 안 함. 따라서 mock user의 conversation 체크포인트가 LangGraph DB에 남는다. 다만 conversation 자체는 agents CASCADE로 삭제 → orphan checkpoint.
- 제안: `_delete_source` 전에 `user_service.cleanup_user_resources(db, source)` 호출. 또는 `delete_user` 사용.

---

## 보안 자가점검 (OWASP)

- **A01 Broken Access Control**: ✓ — 모든 라우터에 `get_current_user` + ownership 검증 (`get_for_user`, `get_owned_conversation`). super_user 분기는 model catalog/system credentials에 일관되게 적용. tool_factory가 system credential 누수 차단(line 192-200).
- **A02 Cryptographic Failures**: ✓ — bcrypt rounds=12 (OWASP 2023), HS256 + 32-byte secret 요구, refresh token SHA-256 hash 저장. 단 dev ephemeral key는 의도된 fallback.
- **A03 Injection**: ✓ — 모든 쿼리가 SQLAlchemy parameterized. `migrate_mock_to_real_user.py`의 `text(f"... {table} {column}")` (line 117-129)는 f-string이지만 화이트리스트(`_REASSIGN_TABLES`)에서만 와서 안전.
- **A05 Security Misconfiguration**: ✓ — `cookie_secure=False`가 dev 기본값이라 production에서 반드시 `true`로 바꿔야 함 (config.py:120 코멘트). HANDOFF에 명시 필요.
- **A07 Identification and Authentication Failures**: ✓ — 5회 실패 시 15분 lockout, refresh rotation + replay → mass-revoke, JWT type 검증, uniform "invalid_credentials" 메시지로 enumeration 차단(`authenticate` line 105-112).
- **A08 Software and Data Integrity Failures**: ✓ — JWT signature 검증, refresh token DB whitelist (forged 토큰은 hash 부재로 거부).
- **A09 Logging Failures**: 코멘트만 — replay 감지 시 WARNING (auth_service:211), super_user 자동 승격 INFO (line 82). 정상 로그인 audit row가 없음 — `last_login_at`만 기록. 운영에서 brute force 추적 시 별도 audit 테이블 필요할 수 있음. (Phase 2 reservation)
- **A10 SSRF**: 해당사항 없음 — auth flow에 외부 fetch 없음. OAuth2 callback은 IdP가 보낸 code만 처리, attacker URL fetch 없음.

---

## 회귀 가능성 (ESCALATION 같은 클래스 grep)

`grep -B3 "raise AppError" auth_service.py | grep "commit"` — 트랜잭션 경계 commit이 raise 직전에 등장하는 두 위치 모두 의도된 fix:
- line 131 (authenticate failed counter)
- line 220 (refresh replay mass-revoke)

다른 service 파일에서 "raise … 직전 commit 누락" 패턴 grep:
- `user_service.py`: 모든 mutator가 flush only — router가 commit 책임. 정상.
- `credentials/service.py`: 동일 패턴, audit_log 항상 router commit 의존. 정상.

**같은 클래스의 잠재 버그 없음.** ESCALATION fix는 핵심 두 곳에 정확히 적용되어 있고, 다른 함수들은 애초에 router-level commit 패턴이라 문제 없음.

---

## 최종 판정

### **CONDITIONAL GO**

**근거**:
- Critical 이슈 0건. ESCALATION 2건 후속 fix 검증 완료.
- High 4건 — 모두 머지 차단할 정도는 아니지만 운영 전 재확인 필요.
  - H2 OAuth callback state 검증 강화 (PoC-grade 명시적)
  - H3 templates 인증 정책 의도 확인
- 보안 자가점검 OWASP 전 항목 통과.
- 테스트 커버리지 (1102 LoC across 6 files) 격리 매트릭스 + replay + CSRF 강제 모두 망라.

**머지 조건**:
1. H3 (templates 라우터 인증 정책) ADR/HANDOFF 명시 — 의도적 공개 confirm.
2. M7 (migration script LangGraph cleanup 누락) follow-up 티켓 등록.
3. 운영 배포 전 `cookie_secure=true`, `JWT_SECRET` (>=32 bytes), `allow_first_user_as_admin=false` 체크리스트 통과.

**Follow-up (별도 PR)**:
- H1 `secrets.compare_digest` 적용
- H2 OAuth callback state user_id 일치 검증
- M1 multi-worker WARNING 메시지 강화
- M5 sessionExpired race 패턴 개선
- M6 LoginForm/RegisterForm maxLength 통일
