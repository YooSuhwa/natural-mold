# HANDOFF — main 동기화 / 다음 작업 정리

**Branch**: `main`
**Date**: 2026-05-18
**최신 커밋**: `3d5a4ea` Merge PR #152 (gitignore)
**Status**: ✅ main clean — 다음 작업 시작 대기

---

## 직전 세션 완료

### PR #151 — deepagents 0.6.1 업그레이드 (머지됨)
- `deepagents` 0.5.6 → 0.6.1, `langgraph` 1.1 → 1.2 (DeltaChannel), `langchain` 1.2 → 1.3
- 동반 회귀 8건 해결: DeltaChannel reconstruction, deadlock, fork-edit/regenerate Overwrite, BranchPicker dedup, middleware 시그니처, WittyLoading remount, 스트리밍 rAF
- 리팩토링: `build_fork_overwrite_input` 헬퍼

### PR #152 — model_catalog gitignore (머지됨)
- `catalog.json` / `fetch_metadata.json` / `sources/*.json` 추적 해제
- `curated/`, `providers.json`, `schema.json`, `litellm_model_catalog.json`(legacy fallback) 유지
- 부재 시 fallback 검증 완료

---

## 남은 할일 (우선순위 순)

### 🔴 즉시 (사용자 직격 / 보안)
1. **Refresh-token replay race** — 두 탭 동시 refresh 시 토큰 전체 revoke. 우리도 오늘 직접 겪음 → 쿠키 수동 삭제 복구. 동시 refresh를 lock 직렬화 or replay 판정 완화. **M effort.**
2. **운영자 환경 셋업** — `JWT_SECRET`(32바이트) / `COOKIE_SECURE=true` / `CORS_ALLOWED_ORIGINS` / `ALLOW_FIRST_USER_AS_ADMIN=false`. **S.**

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
11. **Frontend 사전 회귀 테스트 5건** (i18n 미스매치) —
    `tests/unit/api/client.test.ts`, `providers/query-provider.test.tsx`,
    `sse/parse-sse.test.ts`, `sse/stream-chat.test.ts`. **S.**
12. **중복 system credential 정리** — Anthropic 4 / OpenAI 1 / OpenRouter 1. **XS.**

### 🟣 Phase 2 (장기, 별도 트랙)
13. **Google OAuth 로그인** — **L.**
14. **이메일 검증 + 비번 재설정** — **L.**

---

## 알려진 한계

- **첫 user 메시지 fork-edit** — DeltaChannel ancestor write 누적 이슈는 `Overwrite`로 해결됨. (이미 PR #151에 포함)
- **frontend test 5건 사전 회귀** — 이번 작업과 무관, #11에서 별도 처리.

---

## 관련 파일

| 영역 | 파일 |
|------|------|
| 인증 | `backend/app/services/auth_service.py`, `backend/app/dependencies.py`, `frontend/src/lib/api/client.ts`, `frontend/src/lib/hooks/useAuth.ts` |
| 스트리밍 | `backend/app/agent_runtime/streaming.py`, `frontend/src/lib/chat/use-chat-runtime.ts` |
| Branch tree | `backend/app/services/thread_branch_service.py` |
| Catalog | `backend/app/services/model_metadata.py` (fallback 로직) |

---

## 마지막 상태

- 검증: backend pytest **950 PASS** / ruff clean / frontend tsc clean / build OK
- 워킹트리: 깨끗
- 권장 다음 한 가지: **#1 refresh-token replay race fix** (활성 버그, 사용자 체감 가장 큼)

새 세션 시작: 이 파일 읽고 `git checkout -b fix/refresh-token-race` 같은 식으로 시작하면 됨.
