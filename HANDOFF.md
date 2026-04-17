# 작업 인계 문서

## 최근 완료 (2026-04-17)

**브랜치 `feature/credentials-field-keys-cache` — credentials list N+1 복호화 제거 (백로그 C)**
- Backend: `credentials.field_keys` 비암호화 캐시 컬럼 추가 (`sa.JSON()`, nullable=True) — `app/models/credential.py`
- Backend: 신규 Alembic `m7_add_credential_field_keys` — 컬럼 추가 + 기존 row backfill (ENCRYPTION_KEY 미설정 시 skip + 경고). downgrade는 `op.batch_alter_table`로 SQLite/PostgreSQL 양쪽 호환
- Backend: `credential_service.create_credential` 생성 시 캐시 저장, `update_credential` data 변경 시 동기화(name-only 불변), `extract_field_keys` 캐시 우선 + legacy NULL fallback (ADR-007)
- 신규 테스트 `backend/tests/test_credentials.py` — 5 시나리오 (create/update sync/name-only preserve/list decrypt=0/legacy fallback)
- 문서: `docs/design-docs/adr-007-credentials-field-keys-cache.md`, `docs/exec-plans/active/backlog-c-field-keys-cache.md`
- 검증: backend `ruff check .` PASS, `pytest` **545 passed**, `alembic upgrade/downgrade/upgrade` 왕복 PASS
- 응답 스키마 불변 (`CredentialResponse.field_keys: list[str]` 그대로) — 클라이언트 변경 없음

**PR #48 머지** — 커스텀 도구 credential 통합 (`3a95a9b`)
**PR #47 머지** — MCP 서버 단위 그룹화 (`8dee7e0`)
**PR #46 머지** — 중앙 크리덴셜 관리 (n8n 스타일, Fernet, `/connections`)

## 다음 작업 — 백로그 D: `lazy="joined"` → `selectinload` 전환

범용 성능 개선. `lazy="joined"`로 기본 JOIN되는 관계를 필요 시점에만 `selectinload`/`joinedload` 선택적 로딩하도록 전환하여 불필요한 JOIN을 제거.

- 우선 조사 대상: agents, tools, mcp_servers, credentials 등 관계가 있는 모델
- 기존 쿼리 경로 회귀 검증 필요 (Eager/Lazy 전환이 기대치와 맞는지)

## 백로그 (추천 순서)

| # | 항목 | 규모 | 비고 |
|---|------|------|------|
| ~~C~~ | ~~credentials list N+1 복호화 제거~~ | ~~작음~~ | **완료** (이 브랜치) |
| **D** | **`lazy="joined"` → `selectinload` 전환** | 중 | 범용 성능 개선 |
| E | PREBUILT 공유 행 per-user credential binding | 큼 | 아키텍처 변경 (PoC라 우선순위 낮음) |
| F | `CredentialPickerDialog` 공통 셸 추출 | 중 | prebuilt/custom/mcp-server auth 다이얼로그 3개 중복 제거 |
| G | ToolCard CardFooter 3-way 분기 sub-component 추출 | 작음 | 가독성 개선 (코드 리뷰 잔여) |

## 주의사항

- **ENCRYPTION_KEY 필수** — `.env`에 설정 (없으면 create 503). backfill 마이그레이션은 ENCRYPTION_KEY 없어도 실패 없이 skip + 경고만 (fallback 경로로 legacy row 처리)
- **`extract_field_keys()` 호출 시 from-import 주의** — 패치 대상은 `app.services.credential_service.decrypt_api_key` (credential_service 모듈이 이미 from-import로 바인딩). `app.services.encryption.decrypt_api_key`에 patch 걸어도 서비스 모듈 함수는 재바인딩되지 않음
- **SQLite batch 모드** — drop_column은 반드시 `op.batch_alter_table` 래핑 (SQLAlchemy 2.0 + SQLite 조합)
- pre-existing 깨진 테스트 — `tests/components/chat/*`, `tests/pages/chat.test.tsx`, `tests/pages/agent-*` (별도 정리)
- `.claude/worktrees/` .gitignore 적용됨

## 관련 파일 (백로그 C 완료 기준)

| 목적 | 경로 | 비고 |
|------|------|------|
| 크리덴셜 모델 | `backend/app/models/credential.py` | `field_keys` 컬럼 추가됨 |
| 크리덴셜 서비스 | `backend/app/services/credential_service.py` | create/update/extract 수정됨 |
| 크리덴셜 라우터 | `backend/app/routers/credentials.py` | 변경 없음 (응답 스키마 불변) |
| 응답 스키마 | `backend/app/schemas/credential.py` | 변경 없음 |
| 신규 마이그레이션 | `backend/alembic/versions/m7_add_credential_field_keys.py` | head |
| 신규 테스트 | `backend/tests/test_credentials.py` | 5 시나리오 |
| ADR | `docs/design-docs/adr-007-credentials-field-keys-cache.md` | 승인됨 |

## 마지막 상태

- **브랜치**: `feature/credentials-field-keys-cache` (worktree: `.claude/worktrees/backlog-c`)
- **Base**: `main` @ `3a95a9b` (PR #48 머지)
- **검증**: backend ruff PASS, pytest **545 passed** (신규 5건 포함), alembic 왕복 PASS
- **DB 상태**: `m7_add_credential_field_keys` head — `credentials.field_keys` 컬럼 추가
- **PR 준비**: 단일 커밋 `feat(credentials): field_keys cache column to eliminate N+1 decryption` 권장

## TTH Ralph Loop 통계 (백로그 C)

- 총 스토리: 5 (S0~S4) + M5 통합
- 1회 통과: 5/5 (재시도 없음)
- 에스컬레이션: 0
- 팀 구성: 사티아 + 젠슨(백엔드) + 베조스(QA) — 피차이는 경량팀 구성상 사티아가 ADR 대리 작성
