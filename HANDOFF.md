# HANDOFF — #12b csrfStore 통합 완료

**Branch**: `refactor/csrf-factory-pattern` (PR 미생성)
**Date**: 2026-05-18
**최신 커밋**: `9ac4626` [refactor] csrfStore.set 시그니처 좁히기 + 캐시 의도 주석
**Status**: ✅ #12b 구현 + simplify 완료, PR 생성 대기

---

## 직전 세션 완료

### PR 머지 완료
- #154 — Refresh-token race fix
- #155 — 운영 환경 부팅 보안 셋업 검증
- #156 — RefreshToken GC nightly cron
- #157 — Frontend auth simplify 묶음 (#7-12)
- #158 — next/navigation 글로벌 mock 통합 (#12a)

### #12b — csrf.ts → csrfStore 객체 통합 (PR 대기)
- `csrfStore.set/clear/get` 객체 메서드로 통합 (session-gate.ts `authGate`와 동일 패턴)
- 5 consumer 업데이트: client.ts, parse-sse.ts, session-gate.ts, useAuth.ts
- simplify 후속: `set(token: string)` 타입 좁히기 + warm-cache 주석
- 커밋 2건: `b4a33d9` 통합, `9ac4626` simplify

---

## 남은 할일

### 🟢 후속 작업 (보류 가능)
2b. **race-in-race chain divergence 강화** — Postgres `SELECT FOR UPDATE`. **S. 보안 영향 미미.**
2c. **GC DELETE batch 처리** — `ctid IN ... LIMIT N` 루프. **S. 운영 백로그 감지 시점에.**

### 🟢 deepagents 0.6 후속 트랙
3. **`stream_events(version="v3")` 마이그레이션** — `streaming.py` 분기 단순화. **L. ROI 최대.**
4. **`_collect_checkpoints` Phase 2 병렬화** — `asyncio.gather` + 세마포어. **M.**
5. **CodeInterpreterMiddleware 도입 검토**. **M.**
6. **WittyLoadingMessage 우회책 정공법화** — assistant-ui 재마운트 원인 추적. **M.**

### 🟣 Phase 2 (장기, 별도 트랙)
13. **Google OAuth 로그인** — **L.**
14. **이메일 검증 + 비번 재설정** — **L.**

---

## 알려진 한계

- **race-in-race** — 두 active 토큰 잠시 공존 가능. `_perform_rotation` docstring 명시.
- **GC batch 미분할** — retention=1d 정상 운영 시 영향 없음.

---

## 관련 파일

| 영역 | 파일 |
|------|------|
| 인증 (#1 PR #154) | `backend/app/services/auth_service.py`, `backend/app/models/refresh_token.py`, `backend/alembic/versions/m37_*.py` |
| 운영 검증 (#2 PR #155) | `backend/app/security/production_check.py`, `backend/app/main.py` (lifespan), `docs/operator-setup.md` |
| GC (#2a PR #156) | `backend/app/services/refresh_token_gc.py`, `backend/app/scheduler.py` (`_register_cron_job`), `backend/alembic/versions/m38_*.py` |
| Frontend simplify (#7-12 PR #157) | `frontend/src/lib/api/errors.ts`, `frontend/src/lib/auth/session-gate.ts`, `frontend/src/lib/api/client.ts`, `frontend/src/lib/sse/parse-sse.ts`, `backend/alembic/versions/m39_*.py` |
| Test mocks (#12a PR #158) | `frontend/tests/setup.ts`, 3개 override 파일 |
| csrfStore (#12b) | `frontend/src/lib/auth/csrf.ts`, 4 consumer (client.ts, parse-sse.ts, session-gate.ts, useAuth.ts) |
| 정책 문서 | `docs/design-docs/adr-016-multiuser-auth.md` |

---

## 마지막 상태

- 검증: backend **971 PASS** / ruff clean, frontend **286 PASS** / lint clean / build OK
- 워킹트리: 깨끗 (`refactor/csrf-factory-pattern` 브랜치, 2-커밋)
- 운영 Postgres: m37 + m38 + m39 마이그레이션 적용 완료
- **권장 다음 한 가지**: PR 생성 → 머지 → `/sync` → 🟢 #3 (stream_events v3 마이그레이션, L, ROI 최대) 또는 🟢 #2b (S, 보안 마무리)

새 세션 시작:
1. 이 파일 읽기
2. PR 생성 + 머지
3. `/sync`로 main 복귀
4. 다음 작업 선택
