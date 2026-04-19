# 작업 인계 문서

## 최근 완료 (2026-04-18)

**PR 대기 — 백로그 E M4: CUSTOM Connection 통합** · worktree `.claude/worktrees/backlog-e-m4` / 브랜치 `feature/custom-connection-migration` / base main@`44a39c6`

핵심 변경:
- Alembic `m11_custom_connection`: CUSTOM tool (`tool.credential_id IS NOT NULL AND tool.connection_id IS NULL`) 대상으로 `(user_id, credential_id)` 단위 dedup 후 connection 생성(`type='custom'`, `provider_name='custom_api_key'`, `display_name=[m11-auto-seed] {credential.name}`, `status='active'`, `credential_id` FK 설정) + 그룹 내 tool의 `connection_id` FK 업데이트. `is_default` 은 user×type×provider 당 1개 제약을 존중해 첫 번째만 true, 나머지 false. downgrade는 `[m11-auto-seed]` 마커 LIKE로만 역삭제 (수동 생성분 보호). Idempotent — 재실행 시 기존 connection 재사용
- `app/schemas/tool.py`: `ToolCustomCreate.connection_id?` + `ToolResponse.connection_id?` 필드 추가 (M3 HOTFIX precedent — 모델 컬럼만 있고 응답/요청 스키마 누락이 S4 블로커)
- `app/services/tool_service.py`: `create_custom_tool`에 `connection_id` 패스스루 + ownership/type 가드(`conn.user_id != user_id OR conn.type != 'custom'` → 404). `credential_id` 가드와 대칭
- `app/services/chat_service.py`:
  - `_resolve_custom_auth(tool)` 신규 모듈-private 헬퍼 — PREBUILT `_resolve_prebuilt_auth` 대칭. 3-state fail-closed (connection 지정 + disabled OR credential=NULL → `ToolConfigError`, active+credential → 복호화)
  - `build_tools_config` CUSTOM 분기 분리: `elif tool.type == CUSTOM and tool.connection_id is not None` → `_resolve_custom_auth(tool)` / else → `_resolve_legacy_tool_auth(tool)` (BUILTIN + CUSTOM legacy 공유). M6 cleanup 시 CUSTOM 분기만 정확히 제거 가능
  - Cross-tenant 가드 재사용 (`assert_connection_ownership` / `assert_credential_ownership`)
  - MCP / PREBUILT 분기 시맨틱 변경 0
  - **CUSTOM은 env fallback 없음** — PREBUILT는 "connection 없음 → env fallback(`{}`)"이지만 CUSTOM은 "connection 없음 → legacy fallback(`_resolve_legacy_tool_auth`)". `_resolve_custom_auth` docstring에 명시
- Frontend:
  - `components/tool/add-tool-dialog.tsx` Custom 탭 재배선 — credential 선택/생성 → `useConnections({type:'custom', provider_name:'custom_api_key'})` find-or-create → tool POST body에 `connection_id` 전달 (신규 row는 `credential_id` 미전송, m11 dedup과 중복 회피). 기존 CUSTOM tool의 legacy 표시(credential_id 기반)는 read-only 호환
  - `lib/types/index.ts`: `Tool.connection_id: string | null` + `ToolCustomCreateRequest.connection_id?: string` 추가
  - ko i18n: `tool.addDialog.custom.*` 메시지 보강 (find-or-create UX 안내)

**Scope out (M5/M6 이월)**:
- `custom-auth-dialog.tsx` / `mcp-server-auth-dialog.tsx` → M5 (기존 tool re-bind UI — legacy 경로로만 동작)
- `/connections` 페이지 CUSTOM 섹션 → M5 (PREBUILT 섹션 이미 존재)
- `tools.credential_id` / `tools.auth_config` / `agent_tools.config` / `mcp_server_id` drop → M6

검증: ruff PASS / **pytest 637 passed** (+14 신규, 회귀 0, 1 deselected) / Alembic PG 왕복 PASS (m11 upgrade → downgrade → upgrade, seed marker idempotent) / pnpm lint PASS (0 errors, 1 pre-existing streamError warning) / pnpm build PASS (15 routes)

**이전 머지**: PR #55(M3), #54(M2), #53(M1), #52(M0)

## 다음 작업 — **M5: UI 통합 + F 흡수**

**ADR**: `docs/design-docs/adr-008-connection-entity.md`
**계획**: `docs/exec-plans/active/backlog-e-connection-refactor.md` (§4 M5)

스코프:
- `custom-auth-dialog.tsx` → `ConnectionBindingDialog(type='custom')` 로 교체 (M3 PREBUILT 패턴 재사용)
- `mcp-server-auth-dialog.tsx` → `ConnectionBindingDialog(type='mcp')` 로 교체
- `add-tool-dialog.tsx` MCP 탭 재배선 (Custom 탭은 M4 완료)
- `/connections` 페이지 재편: CUSTOM + MCP 섹션 추가 (PREBUILT 섹션은 M3 완료)
- `agent_tools.connection_id` override UI (에이전트 설정 화면) — 선택적 연결 커스터마이즈
- 3 dialog 중복 제거 확인 (F 완료 처리)

새 세션 진입:
```
docs/exec-plans/active/backlog-e-connection-refactor.md (M5) + adr-008 읽고 M5 시작. worktree/브랜치 새로.
```

## 마일스톤 진행

| M0 ADR | M1 테이블+CRUD | M2 MCP 이관 | M3 PREBUILT | M4 CUSTOM | M5 UI 통합 | M6 Cleanup |
|---|---|---|---|---|---|---|
| PR #52 | PR #53 | PR #54 | PR #55 | **PR 대기** | 다음 | |

## 주의사항 (M4+ 재사용)

- **ENCRYPTION_KEY 필수**
- **M1/M2 학습 유지** (aiosqlite FK 테스트, `is_default` autoflush, PG 전용 SQL 격리, 응답 스키마 write/read 분리, deterministic key, credential 소유권 가드 등)
- **M3 학습 추가**:
  - Model 컬럼 추가 시 **Pydantic 응답 스키마(`ToolResponse` 등)도 동시 점검 필수** — S2 핫픽스로 발견. API-level 응답 필드 화이트리스트 테스트가 조기 검출 장치로 유용
  - Alembic revision ID는 **32자 제한**(alembic_version VARCHAR(32)) — 파일명 ≠ revision ID 허용, m11+도 동일 규칙
  - PREBUILT default connection은 `(user_id, type='prebuilt', provider_name)` 스코프별 1개. Partial unique index로 DB 레벨 강제됨. 시드는 `M10_SEED_MARKER` 같은 display_name 마커로 수동 생성분과 구분하여 downgrade 안전성 확보
  - `user_default_conn_map` 프리로드는 agent_tools의 selectinload로 체인 불가(scope가 tool별이 아니라 user×provider) — 별도 IN 쿼리 1회 + dict 주입이 정답. user_id 필터 누락 시 cross-tenant leak 위험
  - env fallback 2중 처리 금지: chat_service는 `cred_auth={}` 전달만, tool builder(naver_tools.py / google_tools.py) 내부 `or settings.*` 패턴이 env를 책임짐
  - Scope out은 drive-by 금지: S1 삭제 분석에서 "보류 M6" 결정한 항목은 당해 M PR에서 건드리지 않음 (M3에서 custom/mcp dialog 통합을 M4/M5로 이월한 사례)
- **M4 학습 추가**:
  - **M3 HOTFIX precedent 재발동** — M4에서도 Pydantic 스키마 누락(`ToolCustomCreate.connection_id?` + `ToolResponse.connection_id?` 미노출)이 S4 진입 블로커였음. 모델 컬럼 + 응답/요청 스키마 + FE 타입은 **세트**로 체크리스트화 필요. S1 사전 분석에서 이를 예측한 베조스 패턴은 M5에서도 재사용할 것
  - CUSTOM과 PREBUILT의 "connection 없음" 처리가 **대칭이 아님** — PREBUILT는 env fallback(`{}`), CUSTOM은 legacy fallback(`_resolve_legacy_tool_auth`). 이 비대칭을 헬퍼 docstring에 명시해야 향후 일반화 오류 방지
  - `is_default` 제약(user×type×provider 1개)은 m11처럼 한 user가 여러 credential을 가진 경우 **첫 번째만 default=true, 나머지 default=false**로 insert해야 함. Pychai 설계대로. user는 UI에서 추후 default 전환 가능
  - CUSTOM tool `create` 시 ownership 가드는 `credential_id` 뿐 아니라 `connection_id`에도 동등하게 적용 (user_id 일치 + type='custom' 검증). router 404 응답으로 IDOR 차단
  - Scope out 분리는 build_tools_config 분기에도 반영 — CUSTOM과 BUILTIN이 legacy fallback을 공유하더라도 `elif CUSTOM and connection_id / else`로 **구조적으로 분리**해야 M6 cleanup에서 CUSTOM 분기만 정확히 제거 가능
- **Alembic downgrade**: `op.execute("DROP INDEX IF EXISTS …")` + `information_schema` 존재 체크
- **SQLite batch**: `drop_column`은 `op.batch_alter_table` 래핑
- pre-existing 깨진 프론트 테스트: `tests/components/chat/*`, `tests/pages/chat.test.tsx`, `agent-*`

## 마지막 상태

- 브랜치: `feature/custom-connection-migration` (단일 커밋 예정, push 대기)
- Base: main @ `44a39c6` (PR #55 머지 — M3)
- DB head: `m11_custom_connection`
- 보존 worktree: `.claude/worktrees/backlog-e-m1`, `backlog-e-m2`, `backlog-e-m3` (정리 가능)
