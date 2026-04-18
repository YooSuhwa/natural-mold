# CHECKPOINT — 백로그 E M2 · MCP → Connection 이관 (고위험)

**브랜치**: `feature/connection-mcp-migration`
**worktree**: `/Users/chester/dev/natural-mold/.claude/worktrees/backlog-e-m2`
**base**: main @ `29678a1` (PR #53 머지)
**ADR**: `docs/design-docs/adr-008-connection-entity.md`
**실행계획**: `docs/exec-plans/active/backlog-e-connection-refactor.md` (M2 섹션)
**팀**: 피차이(아키텍트/마이그레이션) + 젠슨(chat_service/runtime) + 베조스(회귀 + QA) — 사티아 리드

---

## S0: docs/ 구조 확인

- [x] main에 docs/, ADR-008, exec-plan 존재
- 검증: `ls docs/design-docs/adr-008-connection-entity.md docs/exec-plans/active/backlog-e-connection-refactor.md`
- 상태: **done** (사전 존재)

## S1: 삭제 분석 (베조스)

- [ ] M2 스코프에서 제거 가능한 legacy 코드 식별
- [ ] `tasks/deletion-analysis-e-m2.md` 보고서
- 검증: 보고서 존재, "즉시 삭제 / 단순화 / 보류" 명시
- 상태: pending
- 담당: 베조스
- blockedBy: S0

## S2: Alembic m9 + tools.connection_id (피차이)

- [ ] `backend/alembic/versions/m9_migrate_mcp_to_connections.py`
  - upgrade: `tools.connection_id` UUID nullable FK `connections.id` ON DELETE SET NULL 컬럼 추가
  - upgrade: 각 `mcp_servers` row → `connections` row (type='mcp', provider_name=server.name, display_name=server.name, credential_id=server.credential_id, extra_config={url, auth_type, headers?, env_vars?})
  - upgrade: `tools.mcp_server_id` → 매칭되는 `tools.connection_id` 매핑
  - downgrade: tools.connection_id 값 역매핑, connections의 type='mcp' row 삭제, column drop
- [ ] `backend/app/models/tool.py` — `connection_id` Mapped 컬럼 + `connection` relationship 추가. `mcp_server_id`는 deprecate 주석만 (drop은 M6)
- 검증: `cd backend && uv run alembic upgrade head && uv run alembic downgrade -1 && uv run alembic upgrade head` 왕복 PASS + 기존 pytest 회귀 0
- 상태: pending
- 담당: 피차이
- blockedBy: S0

## S3: chat_service MCP 분기 재작성 + env_vars 템플릿 해석 (젠슨)

- [ ] `backend/app/services/chat_service.py:164-205` MCP 분기 재작성 — `tool.connection_id` → `connections` 경유
- [ ] `backend/app/agent_runtime/` MCP 실행부 (`mcp_client.py` 또는 `executor.py`) — connection.extra_config.url/auth_type/headers 활용
- [ ] env_vars 템플릿 해석기 — `${credential.<field>}` 감지 시 credential.data_decrypted[field] 치환 (런타임 only)
- [ ] legacy fallback: `tool.connection_id IS NULL AND tool.mcp_server_id IS NOT NULL`이면 기존 mcp_servers 경로 유지 (M3~M5 이행 중 안전망). M6에서 제거
- 검증: ruff + 기존 MCP 회귀 PASS
- 상태: pending
- 담당: 젠슨
- blockedBy: S2

## S4: 회귀 + 신규 테스트 (베조스)

- [ ] `tests/test_mcp_connection.py`, `tests/test_tools_router_extended.py` 회귀 갱신
- [ ] 신규 `tests/test_connection_mcp_resolve.py` — connection 경유 MCP 실행 smoke + env_vars 템플릿 해석 검증
- [ ] 마이그레이션 데이터 무결성 테스트
- 검증: `uv run pytest` 회귀 0 (572+ 유지) + 신규 PASS
- 상태: pending
- 담당: 베조스
- blockedBy: S3

## S5: 통합 + 커밋 (사티아)

- [ ] 전체 verify: ruff + pytest + alembic 왕복 + /codex:review
- [ ] HANDOFF.md 업데이트
- [ ] 단일 커밋
- 상태: pending
- blockedBy: S4

---

## 리스크 (M2 고위험 포인트)

1. **데이터 이관 무결성**: mcp_servers row가 누락 없이 connections로 이관
2. **chat_service regression**: hot path, 기존 MCP 도구 실행 즉시 영향
3. **env_vars 템플릿 정합성**: ADR-008 §2 template-only 준수
4. **mcp_servers.auth_config 보존 정책**: 평문값을 extra_config로 옮길지 credential로 승격할지 피차이 판단
