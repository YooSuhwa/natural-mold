# HANDOFF — #12a test mock 통합 완료

**Branch**: `refactor/test-mocks-next-navigation` (PR 미생성)
**Date**: 2026-05-18
**최신 커밋**: `5f63388` [refactor] tests: noise 주석 + 미사용 router stub 제거
**Status**: ✅ #12a 구현 + simplify 완료, PR 생성 대기

---

## 직전 세션 완료

### #1 — Refresh-token race fix (PR #154 머지)
### #2 — 운영 환경 부팅 보안 셋업 검증 (PR #155 머지)
### #2a — RefreshToken GC nightly cron (PR #156 머지)
### #7~12 — Frontend auth simplify 묶음 (PR #157 머지)

### #12a — next/navigation 글로벌 mock + 11파일 중복 제거 (PR 대기)
- `tests/setup.ts`에 글로벌 default mock (useRouter/usePathname/useParams/useSearchParams)
- 8파일 vi.mock 제거 (단순 useRouter anonymous spy)
- 3파일 vi.mock 유지 (named spy / 라우트별 params 필요): conversation-list, chat, agent-detail
- simplify 후속: 7파일 placeholder 주석 제거, `forward`/`refresh` stub YAGNI 제거, override 주석 영문 한 줄로 trim
- 커밋 2건: `e44ead5` 통합, `5f63388` simplify

---

## 남은 할일

### 🟢 후속 작업 (보류 가능)
2b. **race-in-race chain divergence 강화** — Postgres `SELECT FOR UPDATE`. **S. 보안 영향 미미.**
2c. **GC DELETE batch 처리** — `ctid IN ... LIMIT N` 루프. **S. 운영 백로그 감지 시점에.**
12b. **`csrf.ts` factory 패턴 적용** — `session-gate.ts`와 일관성. **XS.**

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
| Test mocks (#12a) | `frontend/tests/setup.ts` (글로벌 mock + per-file override 정책), 3개 override 파일 |
| 정책 문서 | `docs/design-docs/adr-016-multiuser-auth.md` |

---

## 마지막 상태

- 검증: backend **971 PASS** / ruff clean, frontend **286 PASS** / lint clean
- 워킹트리: 깨끗 (`refactor/test-mocks-next-navigation` 브랜치, 2-커밋)
- 운영 Postgres: m37 + m38 + m39 마이그레이션 적용 완료
- **권장 다음 한 가지**: PR 생성 → 머지 → `/sync` → 🟢 #3 (stream_events v3 마이그레이션, ROI 최대) 또는 🟢 #12b/#2b (작은 후속)

새 세션 시작:
1. 이 파일 읽기
2. PR 생성 + 머지
3. `/sync`로 main 복귀
4. 다음 작업 선택
