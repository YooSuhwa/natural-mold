# HANDOFF — #2 운영 환경 셋업 완료

**Branch**: `chore/operator-env-hardening` (PR 미생성)
**Date**: 2026-05-18
**최신 커밋**: `b95ca83` [refactor] production_check: urlsplit + JWT 길이 상수 단일화
**Status**: ✅ #2 구현 + simplify 완료, PR 생성 대기

---

## 직전 세션 완료

### #1 — Refresh-token race fix (PR #154 머지됨)
- 3-way 분기(Live/Race/Replay) + `replaced_by_id` 체인 + 10s grace + UA 바인딩
- m37 마이그레이션 (운영 DB 적용 완료)

### #2 — 운영 환경 부팅 시 보안 셋업 검증 (PR 대기)
- `APP_ENV=production`일 때 위험한 셋업이 있으면 **부팅 거부**
- 검증 5항목: JWT_SECRET(≥32자) / COOKIE_SECURE / ALLOW_FIRST_USER_AS_ADMIN / CORS / ENCRYPTION_KEYS
- 신규: `app/security/production_check.py` (순수 검증) + `docs/operator-setup.md` (체크리스트)
- IPv6 loopback(`::1`, `::`) 누락 버그 simplify 단계에서 발견 + 수정
- 커밋 2건: `99a5edf` 기능, `b95ca83` simplify (urlsplit + MIN_JWT_SECRET_LEN 단일화)
- 966 PASS / ruff clean

---

## 남은 할일 (우선순위 순)

### 🟢 후속 작업 (이번 PR들 정리 후)
2a. **RefreshToken GC job** — revoked row 무한 누적, ADR-016 "30일+1d cron GC" 명시되어 있으나 미구현. APScheduler에 nightly `DELETE WHERE expires_at < now() - 1d`. **S. 운영 진입 전 차단 항목.**
2b. **race-in-race chain divergence 강화** — 두 패자 동시 chain 진입 시 replacement.replaced_by_id overwrite 가능. 보안 영향 미미(docstring 명시). Postgres `SELECT FOR UPDATE`. **S. 보류 OK.**

### 🟢 deepagents 0.6 후속 (다음 트랙)
3. **`stream_events(version="v3")` 마이그레이션** — 가장 큰 ROI. `streaming.py` 분기 단순화. **L.**
4. **`_collect_checkpoints` Phase 2 병렬화** — `asyncio.gather` + 세마포어. **M.**
5. **CodeInterpreterMiddleware 도입 검토** — deepagents 0.6 신기능. **M.**
6. **WittyLoadingMessage 우회책 정공법화** — assistant-ui 재마운트 원인 추적. **M.**

### 🟡 simplify / 위생 (한 PR로 묶기 권장)
7. **`withAuthRetry()`** — apiFetch/apiUpload 401 chain 통합. **S.**
8. **`session-gate.ts` 추출** + `createAuthGate()` factory. **S.**
9. **`readApiErrorBody` 유틸 통합** (3중 중복). **XS.**
10. **`StreamApiError extends ApiError`** — SSE 에러 정규화. **XS.** (#9 후)
11. **Frontend 사전 회귀 테스트 5건** (i18n 미스매치). **S.**
12. **중복 system credential 정리** — Anthropic 4 / OpenAI 1 / OpenRouter 1. **XS.**

### 🟣 Phase 2 (장기, 별도 트랙)
13. **Google OAuth 로그인** — **L.**
14. **이메일 검증 + 비번 재설정** — **L.**

---

## 알려진 한계

- **race-in-race** — 위 #2b 참조. 현재 두 active 토큰 잠시 공존 가능 (둘 다 valid). `_perform_rotation` docstring 명시.
- **RefreshToken GC 미구현** — 위 #2a 참조. 운영 진입 전 필수.
- **frontend test 5건 사전 회귀** — #11에서 별도 처리.

---

## 관련 파일

| 영역 | 파일 |
|------|------|
| 인증 (#1 PR #154) | `backend/app/services/auth_service.py`, `backend/app/models/refresh_token.py`, `backend/alembic/versions/m37_*.py` |
| 운영 검증 (#2) | `backend/app/security/production_check.py`, `backend/app/auth/jwt.py` (`MIN_JWT_SECRET_LEN`), `backend/app/main.py` (lifespan), `backend/.env.example`, `docs/operator-setup.md` |
| 정책 문서 | `docs/design-docs/adr-016-multiuser-auth.md` §4.2, §8.4 |
| 스트리밍 | `backend/app/agent_runtime/streaming.py`, `frontend/src/lib/chat/use-chat-runtime.ts` |
| Catalog | `backend/app/services/model_metadata.py` (fallback) |

---

## 마지막 상태

- 검증: backend pytest **966 PASS** / ruff clean
- 워킹트리: 깨끗 (`chore/operator-env-hardening` 브랜치, 2-커밋)
- 운영 Postgres: m37 마이그레이션 적용 완료
- 운영 부팅 검증: 잘못된 셋업 → RuntimeError 실제 확인, 깨끗한 셋업 → 정상 통과
- **권장 다음 한 가지**: PR 생성 후 머지 → `/sync` → #2a GC job

새 세션 시작:
1. 이 파일 읽기
2. PR 만들고 머지 (`gh pr create`)
3. `/sync`로 main 복귀
4. #2a (GC job) 시작
