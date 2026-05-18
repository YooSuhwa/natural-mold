# HANDOFF — refresh-token race fix 완료 / 다음 작업 정리

**Branch**: `fix/refresh-token-race` (PR 미생성)
**Date**: 2026-05-18
**최신 커밋**: `4b2e25f` [refactor] auth_service: extract rotation/race-head helpers
**Status**: ✅ #1 구현 + simplify 정리 완료, PR 생성 대기

---

## 직전 세션 완료

### #1 — Refresh-token replay race fix (커밋 2건, PR 대기)
- 문제: 두 탭 동시 `/api/auth/refresh` → 패자가 replay 오판 → user 전체 토큰 mass-revoke → 강제 로그아웃
- 해결: 3-way 분기(Live/Race/Replay) + `replaced_by_id` 체인 + 10s grace + UA 바인딩
- 신규 마이그레이션: **m37_refresh_token_replaced_by** (운영 DB 적용 완료, 테스트 fixture는 `create_all` 자동)
- 신규 설정: `refresh_rotation_grace_seconds: int = 10`
- 테스트 4건 추가 (race chain / UA mismatch / grace expired / dead replacement) + 기존 replay 2건은 UA 명시로 보강
- **실제 백엔드 curl 검증 완료** — 3-way 분기 모두 코드 경로 진입 확인
- ADR-016 §4.2 회전 정책 업데이트
- 커밋: `a87846d` 기능, `4b2e25f` simplify 후속 정리(중복 제거 + 헬퍼 추출)

---

## 남은 할일 (우선순위 순)

### 🔴 즉시 (사용자 직격 / 보안)
1. ~~**Refresh-token replay race**~~ → ✅ 완료 (fix/refresh-token-race 브랜치)
2. **운영자 환경 셋업** — `JWT_SECRET`(32바이트) / `COOKIE_SECURE=true` / `CORS_ALLOWED_ORIGINS` / `ALLOW_FIRST_USER_AS_ADMIN=false`. **S.**

### 🆕 이번 PR 후속 작업
2a. **RefreshToken GC job** — revoked row가 무한 누적, ADR-016에 "30일+1d cron GC" 명시되어 있으나 미구현. APScheduler에 nightly `DELETE WHERE expires_at < now() - 1d` 추가. **S. 운영 진입 전 차단 항목.**
2b. **race-in-race chain divergence 강화** — 두 패자가 동시 chain 진입 시 replacement.replaced_by_id overwrite로 고아 active row 발생. 보안 영향 미미(현재 docstring으로 명시)하나 Postgres `SELECT FOR UPDATE`로 invariant 보장 가능. **S. PoC 단계 보류 OK.**

### 🟢 deepagents 0.6 후속 (다음 트랙)
3. **`stream_events(version="v3")` 마이그레이션** — 가장 큰 ROI. `streaming.py` 분기 단순화 + SSE 표준 정합. **L.**
4. **`_collect_checkpoints` Phase 2 병렬화** — `asyncio.gather` + 세마포어. 긴 대화 list_messages 응답시간 ↓. **M.** LangGraph pool 한도 확인 필요.
5. **CodeInterpreterMiddleware 도입 검토** — deepagents 0.6 신기능. **M.**
6. **WittyLoadingMessage 우회책 정공법화** — 모듈 글로벌 캐시 → assistant-ui 재마운트 원인 추적. **M.** 현재 작동 중.

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

- **race-in-race** — 위 #2b 참조. 현재 코드는 두 active 토큰이 잠시 공존 가능(둘 다 valid). docstring(`_perform_rotation`)에 명시.
- **첫 user 메시지 fork-edit** — `Overwrite`로 PR #151에서 해결됨.
- **frontend test 5건 사전 회귀** — #11에서 별도 처리.

---

## 관련 파일

| 영역 | 파일 |
|------|------|
| 인증 (이번 PR) | `backend/app/services/auth_service.py`, `backend/app/models/refresh_token.py`, `backend/app/config.py`, `backend/alembic/versions/m37_refresh_token_replaced_by.py`, `backend/tests/test_auth_refresh.py` |
| 정책 문서 | `docs/design-docs/adr-016-multiuser-auth.md` §4.2 |
| 인증 (관련) | `backend/app/dependencies.py`, `frontend/src/lib/api/client.ts`, `frontend/src/lib/hooks/useAuth.ts` |
| 스트리밍 | `backend/app/agent_runtime/streaming.py`, `frontend/src/lib/chat/use-chat-runtime.ts` |
| Catalog | `backend/app/services/model_metadata.py` (fallback) |

---

## 마지막 상태

- 검증: backend pytest **954 PASS** (기존 950 + 신규 4) / ruff clean
- 워킹트리: 깨끗 (`fix/refresh-token-race` 브랜치)
- 운영 Postgres: m37 마이그레이션 적용 완료
- **권장 다음 한 가지**: PR 생성 후 머지 → `/sync` → #2 운영자 환경 셋업 + #2a GC job

새 세션 시작:
1. 이 파일 읽기
2. PR 만들고 머지 (`gh pr create` 또는 GitHub 웹)
3. `/sync` 로 main 복귀
4. 다음 작업 선택
