# HANDOFF — Multi-User Auth + 라이브 검증 라운드

**Branch**: `feature/multiuser-auth`
**Date**: 2026-05-15
**ADR**: `docs/design-docs/adr-016-multiuser-auth.md`
**Plan**: `~/.claude/plans/replicated-crunching-lark.md`
**Status**: ✅ 코드 안정 — 라이브 검증 fix **19 파일 미커밋**

---

## 마지막 상태

- 커밋 3개: `7e6fcb9` simplify · `4c6a516` feat 멀티유저 · `ec78f3f` chore catalog
- **미커밋 19 파일** (라이브 검증 라운드, 아래 §2 참조)
- 검증: backend 950 PASS / ruff clean, frontend pnpm lint+build PASS
- backend `--reload`로 가동 중 (포트 8001), frontend dev (3000)

---

## 1. 완료 (이전 세션 — 멀티유저 MVP)

- 백엔드 인증 코어 (auth/, JWT/bcrypt/cookie, register/login/logout/refresh/me)
- m36 마이그 + RefreshToken + is_system CHECK + FK CASCADE
- 시드 정리 (mock user 제거, system credentials 전환)
- 6 endpoint `require_super_user` + 63 mutation `verify_csrf`
- 프론트엔드 (proxy.ts, (auth) route, useAuth/useSession, AuthGuard, UserMenu)
- 통합 테스트 5종 + 격리 매트릭스 10/10

---

## 2. 라이브 검증 라운드 (미커밋, 이번 세션)

| 영역 | 핵심 fix |
|------|---------|
| 정책 | **모든 user(super 포함) 에이전트 채팅은 본인 credential만**. System은 service-only |
| credential resolver | 3rd tier provider-matched fallback + `LLMCredentialRequiredError` 422 |
| frontend NEXT_PUBLIC_API_BASE_URL | 8002 → 8001 오타 fix |
| sidebar / system-credentials page | super_user 가드 (UI hide + URL redirect) |
| upload/skill detail | `credentials:'include'` + `apiUpload` 헬퍼 |
| SSE | credentials/CSRF 헤더 + `StreamApiError` + 401/422 inline 처리 |
| useLogout | `window.location.href` hard reload + `cancelQueries` |
| client.ts | `AUTH_PREAUTH_ENDPOINTS` base + refresh dedup + 5s backoff + sessionExpired gate |
| credential POST 500 | `CredentialResponse.user_id` Optional |
| auth_service 트랜잭션 | `record_login_failure`/`rotate_refresh` 후 `commit` 강제 |
| simplify 라운드 | provider dict 통합, endpoint base, instanceof guard, comment 정리 |

전체 변경: 19 파일 (backend 4 + frontend 9 + tests 5 + catalog 6).

---

## 3. 다음 작업 (우선순위)

1. **🔴 미커밋 변경 commit** → push → PR 생성
   - 권장: 2-3개 commit으로 분할 (`[feat]` resolver 정책 / `[fix]` 라이브 검증 fix / `[refactor]` simplify)
2. **🟡 운영자 필수 액션** (ADR-016 §8 + 이전 HANDOFF):
   - `JWT_SECRET` 32바이트 + `COOKIE_SECURE=true` + `CORS_ALLOWED_ORIGINS`
   - 첫 운영자 가입 직후 `ALLOW_FIRST_USER_AS_ADMIN=false`
   - `scripts/migrate_mock_to_real_user.py --dry-run` 후 실행
3. **🟢 후속 PR 분리** (simplify 라운드에서 보존 처리):
   - `session-gate.ts` 추출 + `createAuthGate()` factory
   - `withAuthRetry()` 헬퍼 (apiFetch/apiUpload 401 chain 통합)
   - `readApiErrorBody` 유틸 (3중 중복 통합)
   - `StreamApiError extends ApiError`
4. **Phase 2** (별도 PR): Google OAuth 로그인, 이메일 검증, 비번 재설정

---

## 4. 주의사항

- **건드리지 말 것**: `system_credential_resolver.py` (builder/assistant 전용 경로 — 채팅 resolver와 분리)
- **system credentials 페이지의 OpenAI key** — 사용자가 라이브 검증 중 invalid 키 등록 가능성. 실제 검증 시 [platform.openai.com/api-keys](https://platform.openai.com/api-keys) sk- 키로 재등록 필요
- **중복 row 정리** (사용자 요청 시): `credentials WHERE is_system=true` 에 Anthropic 4개 + OpenAI 1개 + OpenRouter 1개 중복 있음
- **conftest `_stub_llm_credential_resolution` autouse** — 신규 라우터/실행기에서 `resolve_llm_api_key_for_agent` import 시 stub 누락 위험. monkeypatch path 하드코딩됨

---

## 5. 핵심 파일

- `backend/app/agent_runtime/credential_resolution.py` — 4-tier resolver + 422 정책
- `backend/app/credentials/service.py` — `PROVIDER_TO_DEFINITION_KEY` derive
- `frontend/src/lib/api/client.ts` — `apiFetch`/`apiUpload` + auth gates
- `frontend/src/lib/sse/parse-sse.ts` — `StreamApiError` + credentials/CSRF
- `frontend/src/lib/chat/use-chat-runtime.ts` — 422 → inline 메시지
- `frontend/src/lib/hooks/useAuth.ts` — `useLogout` hard reload
- `backend/tests/conftest.py` — autouse credential stub

---

## 6. 검증

```bash
cd backend && uv run ruff check app/ tests/ && uv run pytest -q
cd frontend && pnpm lint && pnpm build
```

기대: ruff clean / 950 PASS / lint clean / build PASS.

다음 세션 첫 행동: 본 파일 + `tasks/code-review-multiuser-auth.md`(이전) 참조.
