# CHECKPOINT — 백로그 E M6 · Cleanup (백엔드 drop + legacy 제거)

**브랜치**: `feature/backlog-e-m6`
**worktree**: `/Users/chester/dev/natural-mold/.claude/worktrees/backlog-e-m6`
**base**: main @ `ad8c0fd` (PR #58 머지 — M5 UI 통합 + F 흡수)
**ADR 참조**: `docs/design-docs/adr-008-connection-entity.md`
**실행계획 참조**: `docs/exec-plans/active/backlog-e-connection-refactor.md` (§5 M6)
**팀**: 베조스(삭제 분석 + 회귀) + 피차이(마이그레이션 설계) + 젠슨(백엔드 구현) + 저커버그(프론트 type/API thin cleanup) — 사티아 리드

---

## 스코프 합의 (2026-04-21, 사용자 승인 — 2차 축소)

**축소 사유**: 베조스 S1 분석에서 `connection-binding-dialog.tsx:479`가 `useUpdateMCPServer`를 라이브 호출 중임을 확인. M5가 의도적으로 옵션 D를 M6로 넘겼으나 사용자가 옵션 D는 M6.1로 분리하기로 함 → `mcp_servers` 관련 drop 불가능 → M6는 **auth_config/credential_id/agent_tools.config 만** drop.

| 항목 | 결정 |
|------|------|
| M6 스코프 | **축소 cleanup** — `tools.auth_config` + `tools.credential_id` + `agent_tools.config` drop. MCP 관련은 보류 |
| `mcp_servers` 테이블 drop | **보류 → M6.1** (옵션 D 선행 필요) |
| `tools.mcp_server_id` drop | **보류 → M6.1** |
| `credential_service.resolve_server_auth` | **유지** — MCP 경로가 여전히 라이브 |
| `chat_service` MCP legacy fallback | **유지** — MCP resolve가 여전히 필요 |
| `chat_service` CUSTOM bridge override | **제거** — `tool.credential_id` drop과 동반 |
| `chat_service` `merged_auth` agent_tools.config merge | **제거** |
| 옵션 D (PATCH tools connection_id) | 보류 → M6.1 |
| M5.5 (agent_tools.connection_id override) | M6.1 이후 |
| 신규 프론트 기능 / 리디자인 | 금지 |

---

## M6 제거 대상 (축소 스코프 locked)

**DB 스키마 (m12)**
- `tools.auth_config` 컬럼 drop
- `tools.credential_id` 컬럼 + FK drop
- `agent_tools.config` 컬럼 drop

**DB 스키마 (M6 유지)**
- `tools.mcp_server_id` (live via MCPServer CRUD)
- `mcp_servers` 테이블
- `fk_tools_mcp_server_id`

**백엔드 코드 제거**
- `chat_service.py` CUSTOM bridge override (tool.credential_id != connection.credential_id 분기)
- `chat_service.py` `merged_auth = {**cred_auth, **(link.config or {})}` → `cred_auth`만
- `chat_service.py` `_resolve_legacy_tool_auth`에서 CUSTOM 분기 제거 → fail-closed ToolConfigError. MCP/BUILTIN 분기는 유지 (mcp 보류) — S1 지침에 따라 전체 삭제 가능 시 제거
- `schemas/agent.py` `ToolConfigEntry` / `tool_configs` / `agent_config` (프론트 dead transmit)
- `agent_service.py` tool_configs 처리 블록
- `agent_runtime/assistant/tools/write_tools.py` `update_tool_config` + `read_tools.py` config 반환
- `schemas/tool.py` `ToolResponse.auth_config` + `_mask_auth_config`
- `models/tool.py` `Tool.auth_config` / `Tool.credential_id` / `Tool.credential` relationship / `AgentToolLink.config`

**백엔드 코드 유지 (M6.1에서 처리)**
- `credential_service.resolve_server_auth()` + 호출처
- `chat_service.py` MCP legacy fallback 블록
- `tool_service.py` MCPServer CRUD 4종
- `routers/tools.py` `/api/tools/mcp-server*` 4개 엔드포인트
- `schemas/tool.py` `MCPServerCreate`/`MCPServerResponse` + `ToolResponse.mcp_server_id`
- `models/tool.py` `MCPServer` 클래스 + `Tool.mcp_server`/`mcp_server_id`

**프론트 thin cleanup**
- `lib/api/tools.ts` `updateAuthConfig` 내 `auth_config`/`credential_id` 전달 제거 (함수 자체 dead 여부 S4에서 판단)
- `lib/types/*.ts` `Tool`에서 `auth_config`/`credential_id` 제거, `AgentTool`에서 `config` 제거
- `Tool.mcp_server_id` 타입은 **유지** (live)
- MCP 관련 hook (`useUpdateMCPServer` 등)은 **유지**
- 참조처는 삭제 or type-narrow만

---

## S0: M5 아카이브 + 새 진행 상태 초기화 [사티아] — 완료

- [x] M5 progress.txt/CHECKPOINT.md/AUDIT.log → `tasks/archive/*-backlog-e-m5.*`
- [x] 새 CHECKPOINT.md, progress.txt, AUDIT.log 초기화

## S1: 삭제 분석 (베조스) [blockedBy: S0]

- [ ] `tasks/deletion-analysis-e-m6.md` 작성
  - 제거 대상을 **파일:라인** 단위로 정확히 확정
  - **삭제(D) / 단순화(S) / 유지(K)** 태그 분류
  - `agent_tools.config` merge 로직(`chat_service.py:445`) 처리 방안 확정:
    - `tools_config` 주입 경로가 이 필드를 사용하는지 재확인
    - 사용 중이면 대체 경로 제안 or scope 축소 제안
  - 테스트 파일 중 "전체 삭제" vs "legacy 시나리오만 삭제" 분리
- [ ] **scope creep 차단**: 옵션 D / UI 리팩토링 / M5.5 변경 0건 명시
- 검증: 보고서 존재, 파일:라인 명시, drive-by 금지

## S2: m12 마이그레이션 설계 (피차이) [blockedBy: S0]

- [ ] `docs/design-docs/m6-cleanup-migration-spec.md` 작성
  - upgrade 순서: FK drop → column drop → table drop
  - downgrade 전략: **구조만 복구, 데이터 복구 불가** 명시
  - pre-check 쿼리: `SELECT count(*) FROM tools WHERE credential_id IS NOT NULL AND connection_id IS NULL` → 0 기대
  - 모델 레이어 변경 스펙
- [ ] m12 revision ID convention: `m12_drop_legacy_columns`
- 검증: 설계 문서 존재, 젠슨이 읽고 바로 구현 가능

## S3: 백엔드 legacy 코드 제거 (젠슨) [blockedBy: S1, S2]

- [ ] `backend/alembic/versions/m12_drop_legacy_columns.py` 신규
- [ ] `models/tool.py`: MCPServer 클래스 + Tool legacy FK/관계 + AgentToolLink.config 삭제
- [ ] `services/credential_service.py`: `resolve_server_auth()` 삭제
- [ ] `services/chat_service.py`: MCP legacy fallback / CUSTOM bridge override / agent_tools.config merge 제거
- [ ] `services/tool_service.py`: MCPServer CRUD 4종 삭제
- [ ] `routers/tools.py`: `/api/tools/mcp-server*` 4개 엔드포인트 삭제
- [ ] `schemas/tool.py`: MCPServerCreate/Response + ToolResponse legacy 필드 + `_mask_auth_config` 삭제
- [ ] `agent_runtime/`: legacy 필드 참조 정리
- [ ] 테스트: MCP legacy 시나리오만 삭제 (connection path 테스트 유지)
- 검증: `uv run alembic upgrade head && uv run alembic downgrade -1 && uv run alembic upgrade head` PASS, ruff PASS, pytest PASS (허용 감소 후 0 regression)

## S4: 프론트 dead API/type 제거 (저커버그) [blockedBy: S3 스키마 결정]

- [ ] `lib/api/tools.ts`: MCPServer CRUD 4종 + updateAuthConfig legacy 필드 제거
- [ ] `lib/types/*.ts`: Tool/AgentTool 레거시 필드 제거
- [ ] 타입 참조처: 빌드 에러 터지는 곳만 **삭제** (신규 로직 금지)
- [ ] `use-chat-runtime.ts:74` streamError unused warning은 건드리지 말 것 (기존 부채)
- 검증: `pnpm lint` (기존 1건 외 0), `pnpm build` PASS

## S5: 통합 검증 + 수동 회귀 (베조스) [blockedBy: S3, S4]

- [ ] `tasks/manual-e2e-e-m6.md` 5개 시나리오
  1. PREBUILT connection → Naver 도구 → 에이전트 실행
  2. CUSTOM connection → 사용자 도구 → 에이전트 실행
  3. MCP connection → MCP 도구 → 에이전트 실행
  4. Connection 비활성화 → fail-closed 에러
  5. DB 직접: `SELECT * FROM mcp_servers` → relation does not exist
- [ ] pytest / ruff / pnpm lint / pnpm build 전체 PASS
- 검증: 전체 그린, 수동 E2E 5/5 PASS

## S6: HANDOFF.md + 단일 커밋 (사티아) [blockedBy: S5]

- [ ] HANDOFF.md M6 상태 반영 (M5.5 / M6.1 로드맵 업데이트)
- [ ] 전체 verify 재확인
- [ ] 단일 커밋 → push는 사용자에게 위임

---

## 리스크

1. `agent_tools.config` merge 로직 — 실제 사용 여부 S1에서 재확인 필수
2. FK drop 순서 오류 — credential_id/mcp_server_id FK ondelete 확인
3. pre-check 데이터 누락 — `credential_id IS NOT NULL AND connection_id IS NULL` row 존재 시 migration 실패
4. 테스트 삭제 과다 — connection path 회귀 커버리지 손실 금지
5. 프론트 타입 cascade — 모든 참조처 grep 후 제거
6. downgrade 불가능성 — 프로덕션 가이드에 명시

---

## 검증 커맨드

```bash
cd backend
uv run alembic upgrade head
uv run alembic downgrade -1 && uv run alembic upgrade head
uv run ruff check .
uv run pytest

cd ../frontend
pnpm lint
pnpm build

# 스키마 검증 (프로덕션 PostgreSQL)
psql -U moldy -d moldy -c "\d tools"
psql -U moldy -d moldy -c "\d agent_tools"
psql -U moldy -d moldy -c "SELECT to_regclass('mcp_servers')"

# legacy 흔적 grep (0 기대)
rg "mcp_server_id|auth_config|resolve_server_auth" backend/app/
rg "MCPServer|register_mcp_server" backend/app/
```
