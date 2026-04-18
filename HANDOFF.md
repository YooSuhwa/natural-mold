# 작업 인계 문서

## 최근 완료 (2026-04-18)

**PR 대기 — 백로그 E M3: PREBUILT per-user Connection** · worktree `.claude/worktrees/backlog-e-m3` / 브랜치 `feature/prebuilt-per-user-connection` / base main@`b34125d`

핵심 변경:
- Alembic `m10_prebuilt_connection`: `tools.provider_name VARCHAR(50)` 컬럼 추가 + 14개 PREBUILT seed tool name 백필(naver 5 / google_search 3 / google_workspace 5 / google_chat 1) + mock user env → credential → default connection 자동 시드 (idempotent, `[m10-auto-seed]` 마커로 downgrade 보호)
- `app/models/tool.py`: `provider_name: Mapped[str | None] = mapped_column(String(50))` 추가
- `app/schemas/tool.py`: `ToolResponse.provider_name: str | None = None` 추가 (API 응답에 필드 포함)
- `app/seed/default_tools.py`: 14개 PREBUILT tool dict에 `provider_name` 필드 부여 (신규 install에서 자동 반영)
- `app/services/connection_service.py`: `get_default_connection(db, user_id, type, provider_name)` sync 헬퍼 추가 (기존 시그니처 보존)
- `app/services/chat_service.py`:
  - `get_agent_with_tools` 내부에서 PREBUILT tool의 distinct provider_name set 추출 → user_id IN 필터로 `user_default_conn_map` 1회 로드(N+1 방지, 테스트 가능한 private 헬퍼로 분리)
  - `build_tools_config` PREBUILT 분기 재작성: `(user_id, provider_name, is_default=true)` connection 기반 credential 해석. connection 없으면 `cred_auth={}` → tool builder 내부 env fallback(naver_tools/google_tools의 `or settings.*` 패턴) 유지
  - Legacy fallback: `provider_name IS NULL` tool은 기존 `tool.credential_id` 경로 유지 (M6까지 이행 tolerance)
  - CUSTOM 분기는 M4 대상 — 시맨틱 변경 0
  - Cross-tenant 가드 재사용 (`assert_connection_ownership` / `assert_credential_ownership`)
- Frontend:
  - 신규 `components/connection/ConnectionBindingDialog.tsx` 공통 셸 (PREBUILT 전용, M3 스코프)
  - `components/tool/prebuilt-auth-dialog.tsx` 얇은 어댑터화 + detectProvider 휴리스틱 제거 (API 응답 `tool.provider_name` 직접 사용)
  - `app/connections/page.tsx` 에 PrebuiltConnectionSection 추가 (기존 CredentialCard 리스트 유지 — M5 이월)
  - 신규 `lib/api/connections.ts` + `lib/hooks/use-connections.ts` (scope-wide invalidation)
  - `lib/types/index.ts`: `Tool.provider_name`, Connection 관련 타입 추가
  - ko i18n: `connections.bindingDialog.*`, `connections.prebuiltSection.*`, `tool.authDialog.legacyUnavailable`

**스코프 조정 (내부 리드 판단으로 승인됨)**:
- 최초 스폰 지시의 "3 dialog 동시 재배선"을 **PREBUILT 전용**으로 축소
- `custom-auth-dialog.tsx` / `mcp-server-auth-dialog.tsx` → M4/M5 이월 (drive-by 금지 원칙, S1 분석서 권고)

검증: ruff PASS / **pytest 614 passed** (+10, 회귀 0) / Alembic PG 왕복 PASS / pnpm lint PASS (0 errors, 1 pre-existing warning) / pnpm build PASS (14 routes)

**이전 머지**: PR #54(M2), #53(M1), #52(M0)

## 다음 작업 — **M4: CUSTOM Connection 통합**

**ADR**: `docs/design-docs/adr-008-connection-entity.md` §3 (CUSTOM)
**계획**: `docs/exec-plans/active/backlog-e-connection-refactor.md` (M4)

스코프:
- CUSTOM 도구 해석 경로도 `tool.connection_id` 경유로 전환 (현재는 `tool.credential_id` 직접 참조)
- Alembic `m11_migrate_custom_credentials` — 기존 `tool.credential_id` 있는 CUSTOM 도구 → connection 생성 후 FK 설정
- `tools.credential_id`는 이 시점부터 deprecated (drop은 M6)
- Frontend: `components/tool/custom-auth-dialog.tsx` → ConnectionBindingDialog(type='custom')으로 재배선
- `add-tool-dialog.tsx` Custom 탭이 connection 생성하도록 재배선

새 세션 진입:
```
docs/exec-plans/active/backlog-e-connection-refactor.md (M4) + adr-008 읽고 M4 시작. worktree/브랜치 새로.
```

## 마일스톤 진행

| M0 ADR | M1 테이블+CRUD | M2 MCP 이관 | M3 PREBUILT | M4 CUSTOM | M5 UI 통합 | M6 Cleanup |
|---|---|---|---|---|---|---|
| PR #52 | PR #53 | PR #54 | **PR 대기** | 다음 | | |

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
- **Alembic downgrade**: `op.execute("DROP INDEX IF EXISTS …")` + `information_schema` 존재 체크
- **SQLite batch**: `drop_column`은 `op.batch_alter_table` 래핑
- pre-existing 깨진 프론트 테스트: `tests/components/chat/*`, `tests/pages/chat.test.tsx`, `agent-*`

## 마지막 상태

- 브랜치: `feature/prebuilt-per-user-connection` (단일 커밋 예정, push 대기)
- Base: main @ `b34125d` (PR #54 머지 — M2)
- DB head: `m10_prebuilt_connection`
- 보존 worktree: `.claude/worktrees/backlog-e-m1`, `backlog-e-m2` (정리 가능)
