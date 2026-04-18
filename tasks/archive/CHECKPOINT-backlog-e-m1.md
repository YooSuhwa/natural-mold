# CHECKPOINT — 백로그 E M1 · Connection 테이블 + CRUD API

**브랜치**: `feature/connections-table`
**worktree**: `/Users/chester/dev/natural-mold/.claude/worktrees/backlog-e-m1`
**ADR**: `docs/design-docs/adr-008-connection-entity.md`
**실행계획**: `docs/exec-plans/active/backlog-e-connection-refactor.md`
**시작**: 2026-04-18
**팀**: 피차이(아키텍트) + 젠슨(구현) + 베조스(QA) — 사티아 리드

---

## S0: docs/ 구조 확인

- [x] 이미 `docs/`, `docs/design-docs/`, `docs/exec-plans/active/` 구조 존재
- [x] ADR-008 이미 존재 (main 머지됨)
- 검증: `ls docs/ARCHITECTURE.md docs/design-docs/index.md docs/design-docs/adr-008-connection-entity.md`
- done-when: 3 파일 모두 존재
- 상태: **done** (사전 존재)

## S1: 삭제 분석 (베조스)

- [ ] M1 스코프에서 제거/단순화 후보 식별 (drive-by 금지)
- [ ] `tasks/deletion-analysis-e-m1.md` 보고서 작성
- 검증: 보고서 존재 + 결론이 "제거 X건, 단순화 Y건, 보류 Z건" 형식으로 명시
- done-when: 사티아 승인
- 상태: pending
- 담당: 베조스

## S2: Connection 모델 + 스키마 + Validator (피차이)

- [ ] `backend/app/models/connection.py` 신규 — ADR-008 §1 스키마 (user_id NOT NULL, type/provider_name/display_name/credential_id/extra_config/is_default/status/created_at/updated_at, 인덱스 `(user_id, type, provider_name)`)
- [ ] `backend/app/schemas/connection.py` 신규 — `ConnectionCreate`, `ConnectionUpdate`, `ConnectionResponse`
  - `provider_name` validator: type='prebuilt'이면 credential_registry enum 5종 제약, 아니면 영문/숫자/언더스코어 문자열
  - MCP validator: `extra_config.url` 필수
- [ ] `backend/app/models/__init__.py`에 Connection export
- 검증: `cd backend && uv run ruff check app/models/connection.py app/schemas/connection.py && uv run python -c "from app.models import Connection; from app.schemas.connection import ConnectionCreate"`
- done-when: ruff PASS + import 순환 없음
- 상태: pending
- 담당: 피차이
- blockedBy: S0

## S3: Service + Router + Migration (젠슨)

- [ ] `backend/alembic/versions/m8_add_connections.py` — upgrade: 테이블 + 인덱스 생성, downgrade: drop
- [ ] `backend/app/services/connection_service.py` — CRUD + `is_default` 원자 토글 (같은 user_id+type+provider_name 범위 내 기존 default 해제)
- [ ] `backend/app/routers/connections.py` — `GET /api/connections`, `GET /api/connections/{id}`, `POST /api/connections`, `PATCH /api/connections/{id}`, `DELETE /api/connections/{id}` (모두 `get_current_user` 필터)
- [ ] `backend/app/main.py` — router 등록
- 검증: `cd backend && uv run ruff check . && uv run alembic upgrade head && uv run alembic downgrade -1 && uv run alembic upgrade head`
- done-when: 왕복 PASS, ruff PASS
- 상태: pending
- 담당: 젠슨
- blockedBy: S2

## S4: 테스트 (베조스)

- [ ] `backend/tests/test_connections.py` 신규 — ADR-008 §M1 테스트 시나리오 8개:
  1. CRUD 기본 (credential 연결 + NULL)
  2. MCP validator (extra_config.url 없으면 422)
  3. PREBUILT validator (non-enum provider_name은 422)
  4. is_default 자동 설정 (첫 connection)
  5. is_default 토글 원자성 (기존 default 자동 해제)
  6. IDOR 방지 (user_A가 user_B 리소스 접근 시 404)
  7. credential ON DELETE SET NULL
  8. extra_config 타입 불일치 (PREBUILT에 주면 경고/무시)
- [ ] 전체 회귀 pytest PASS (545+ 유지)
- 검증: `cd backend && uv run pytest tests/test_connections.py -v && uv run pytest`
- done-when: 신규 8 시나리오 통과 + 기존 회귀 0
- 상태: pending
- 담당: 베조스
- blockedBy: S3

## S5: 통합 + 커밋 (사티아)

- [ ] 전체 verify: ruff + pytest + alembic 왕복 PASS
- [ ] HANDOFF.md 업데이트
- [ ] 단일 커밋
- 검증: `git log --oneline feature/connections-table ^main`
- done-when: 커밋 존재, verify PASS
- 상태: pending
- 담당: 사티아
- blockedBy: S4
