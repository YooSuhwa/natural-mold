# HANDOFF — #2a RefreshToken GC 완료

**Branch**: `chore/refresh-token-gc-job` (PR 미생성)
**Date**: 2026-05-18
**최신 커밋**: `f03d3d5` [refactor] scheduler: _register_cron_job 헬퍼로 4개 잡 통합
**Status**: ✅ #2a 구현 + simplify (인덱스 + fixture + cron 헬퍼) 완료, PR 생성 대기

---

## 직전 세션 완료

### #1 — Refresh-token race fix (PR #154 머지)
- 3-way 분기(Live/Race/Replay) + `replaced_by_id` 체인 + 10s grace + UA 바인딩
- m37 마이그레이션 (운영 DB 적용 완료)

### #2 — 운영 환경 부팅 시 보안 셋업 검증 (PR #155 머지)
- `APP_ENV=production` + 위험 셋업 → **부팅 거부**
- 검증 5항목: JWT_SECRET / COOKIE_SECURE / ALLOW_FIRST_USER_AS_ADMIN / CORS / ENCRYPTION_KEYS
- IPv6 loopback(`::1`, `::`) 누락 버그 simplify 단계에서 발견 + 수정

### #2a — RefreshToken GC nightly cron (PR 대기)
- `register_refresh_token_gc_job()` — 매일 05:00 UTC에 `DELETE WHERE expires_at < now - retention_days`
- m38 마이그레이션: `ix_refresh_tokens_expires_at btree` (운영 DB 적용 완료)
- `_register_cron_job` 헬퍼 추출 (4개 cron 잡 통합, 38줄 감소)
- 테스트 fixture를 conftest로 통합 (`make_user`, `make_refresh_token`)
- 커밋 3건: `77cd75f` 기능, `2850937` 인덱스/fixture, `f03d3d5` cron 헬퍼
- 971 PASS / ruff clean

---

## 남은 할일 (우선순위 순)

### 🟢 후속 작업 (이번 PR들 정리 후)
2b. **race-in-race chain divergence 강화** — 두 패자 동시 chain 진입 시 replacement.replaced_by_id overwrite 가능. 보안 영향 미미(docstring 명시). Postgres `SELECT FOR UPDATE`. **S. 보류 OK.**
2c. **GC DELETE batch 처리** — 현재 단일 트랜잭션. retention=1d 운영 시 일 수천 건이라 OK이나 누적 백로그 cleanup 시나리오에 long-running tx + autovacuum 지연 가능. `ctid IN (... LIMIT N)` 루프 패턴. **S. 운영 백로그 감지 시점에.**

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
- **GC batch 미분할** — 위 #2c 참조. retention=1d 정상 운영 시 영향 없음.
- **frontend test 5건 사전 회귀** — #11에서 별도 처리.

---

## 관련 파일

| 영역 | 파일 |
|------|------|
| 인증 (#1 PR #154) | `backend/app/services/auth_service.py`, `backend/app/models/refresh_token.py`, `backend/alembic/versions/m37_*.py` |
| 운영 검증 (#2 PR #155) | `backend/app/security/production_check.py`, `backend/app/auth/jwt.py` (`MIN_JWT_SECRET_LEN`), `backend/app/main.py` (lifespan), `docs/operator-setup.md` |
| GC (#2a) | `backend/app/services/refresh_token_gc.py`, `backend/app/scheduler.py` (`_register_cron_job`, `register_refresh_token_gc_job`), `backend/alembic/versions/m38_*.py`, `backend/tests/conftest.py` (factory) |
| 정책 문서 | `docs/design-docs/adr-016-multiuser-auth.md` §4.2, §8.4 |
| 스트리밍 | `backend/app/agent_runtime/streaming.py`, `frontend/src/lib/chat/use-chat-runtime.ts` |
| Catalog | `backend/app/services/model_metadata.py` (fallback) |

---

## 마지막 상태

- 검증: backend pytest **971 PASS** / ruff clean
- 워킹트리: 깨끗 (`chore/refresh-token-gc-job` 브랜치, 3-커밋)
- 운영 Postgres: m37 + m38 마이그레이션 적용 완료, `ix_refresh_tokens_expires_at` 인덱스 생성 확인
- 부팅 검증: GC 잡 등록 로그 확인 (`Scheduled refresh-token GC: cron 0 5 * * * (retention=1d)`)
- **권장 다음 한 가지**: PR 생성 후 머지 → `/sync` → 🟡 simplify 묶음(#7~#12) 또는 🟢 deepagents 0.6 트랙(#3~#6) 중 선택

새 세션 시작:
1. 이 파일 읽기
2. PR 만들고 머지 (`gh pr create`)
3. `/sync`로 main 복귀
4. 다음 작업 선택 (#2b race-in-race 또는 #2c GC batch는 보류 OK)
