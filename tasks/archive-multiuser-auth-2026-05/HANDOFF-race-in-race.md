# HANDOFF — #2b race-in-race fix 완료

**Branch**: `fix/refresh-token-race-in-race` (PR 미생성)
**Date**: 2026-05-18
**최신 커밋**: `de61260` [refactor] is_postgres 헬퍼 + 초기 SELECT를 FOR UPDATE로 통합
**Status**: ✅ #2b 구현 + simplify 완료, PR 생성 대기

---

## 직전 세션 완료

### PR 머지 완료
- #154 — Refresh-token race fix
- #155 — 운영 환경 부팅 보안 셋업 검증
- #156 — RefreshToken GC nightly cron
- #157 — Frontend auth simplify 묶음 (#7-12)
- #158 — next/navigation 글로벌 mock (#12a)
- #159 — csrfStore 통합 (#12b)

### #2b — race-in-race chain divergence 강화 (PR 대기)
- `_lock_select(stmt, db)`: Postgres `SELECT FOR UPDATE` 적용 헬퍼 (SQLite no-op)
- `rotate_refresh`: chain-walk 루프 (`_MAX_CHAIN_FOLLOW=5`) — 락 후 재검증 → live/race/replay 분기. 락 패배 측이 체인 1 hop 전진 후 재시도 → orphan active row 없음
- `is_postgres(db)` 헬퍼 추가 (app/database.py), spend_writer 중복 제거
- 초기 SELECT를 FOR UPDATE와 통합 → hot path RTT 1 절감
- 신규 테스트: chain depth limit 시뮬레이션 (monkeypatch cycle)
- ADR-016 §4.2에 lock 정책 + chain-walk 동작 명시
- 커밋 2건: `c4953a9` 기능, `de61260` simplify

---

## 남은 할일

### 🟢 후속 작업 (보류 가능)
2c. **GC DELETE batch 처리** — `ctid IN ... LIMIT N` 루프. **S. 운영 백로그 감지 시점에.**
**🆕 oauth2_base FOR UPDATE 누락 가능성** — `backend/app/credentials/oauth2_base.py:5` 주석에 "caller가 SELECT FOR UPDATE 책임" 명시되어 있으나 실제 구현 미확인. OAuth 토큰 동시 refresh 비직렬화 가능성. simplify 리뷰에서 발견. **S. 조사 + 보강.**
**🆕 streaming.py 타입 기반 분기 정리** — `msg.type in (...)` 문자열 체크를 `isinstance(msg, AIMessageChunk)` 등 타입 기반으로 일부 교체. 분기 수 동등, 가독성/타입 안전성↑. 영향: `streaming.py` ~라인 289-360. **XS.**

### 🟢 deepagents 0.6 후속 트랙
~~3. `stream_events(version="v3")` 마이그레이션~~ — **폐기 (2026-05-18)**. `streaming.py:273`은 이미 LangGraph 정식 권장 `astream(stream_mode="messages")` 사용 중, v3는 beta + 콜백 이벤트 미사용 코드엔 이득 없음 + 분기 수 동등. 근거: `langchain_core/runnables/base.py:1495` (v3는 BaseChatModel/CompiledGraph 전용, experimental). 가능한 작은 정리는 위 🆕 항목으로 분리.
3. **`_collect_checkpoints` Phase 2 병렬화** — `asyncio.gather` + 세마포어. **M.**
4. **CodeInterpreterMiddleware 도입 검토**. **M.**
5. **WittyLoadingMessage 우회책 정공법화** — assistant-ui 재마운트 원인 추적. **M.**

### 🟣 Phase 2 (장기, 별도 트랙)
13. **Google OAuth 로그인** — **L.**
14. **이메일 검증 + 비번 재설정** — **L.**

---

## 알려진 한계

- **race-in-race 실 동시성 검증** — SQLite 테스트 환경 한계. Postgres 통합 테스트 별도 필요 (ADR-016 §4.2에 명시).
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
| csrfStore (#12b PR #159) | `frontend/src/lib/auth/csrf.ts`, 4 consumer |
| race-in-race (#2b) | `backend/app/services/auth_service.py` (chain-walk + `_lock_select`), `backend/app/database.py` (`is_postgres`), `backend/app/services/spend_writer.py` |
| 정책 문서 | `docs/design-docs/adr-016-multiuser-auth.md` |

---

## 마지막 상태

- 검증: backend **972 PASS** / ruff clean, frontend **286 PASS** / lint clean / build OK
- 워킹트리: 깨끗 (`fix/refresh-token-race-in-race` 브랜치, 2-커밋)
- 운영 Postgres: m37 + m38 + m39 마이그레이션 적용 완료
- **권장 다음 한 가지**: 🟢 oauth2_base FOR UPDATE 조사 (S, 직전 PR 후속) 또는 deepagents 트랙 #3 (`_collect_checkpoints` 병렬화, M)

새 세션 시작:
1. 이 파일 읽기
2. PR 생성 + 머지
3. `/sync`로 main 복귀
4. 다음 작업 선택
