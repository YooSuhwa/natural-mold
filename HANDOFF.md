# 작업 인계 문서

## 최근 완료 (2026-04-20)

**백로그 E M5: UI 통합 + F 흡수 (프론트 전용) — 머지 대기**
worktree `.claude/worktrees/backlog-e-m5` / 브랜치 `feature/backlog-e-m5` / base main@`12d3d18`

### 핵심 변경 (17 파일, +1047/-881, 백엔드 0건)
- `ConnectionBindingDialog` type discriminated union 확장 (prebuilt/custom/mcp) — 3 legacy dialog 흡수
- `app/connections/page.tsx` 전면 재작성: PREBUILT/CUSTOM/MCP 3섹션, `ConnectionCard` + `ConnectionDetailSheet` 신규
- `PrebuiltProps.connectionId` 추가 → drawer가 selected row 직접 update (default 덮어쓰기 방지)
- `useToolsByConnection` selector (PREBUILT=default만, MCP=credential 이중 hop)
- `handleCustomBound` mutateAsync + onError → silent partial failure 차단
- McpBody: 단일 connection만 안전 sync, N:1 공유는 `sharedCredentialWarning` + server PATCH만 (cross-server mutation 차단)
- dead 3 dialog 파일 삭제(`prebuilt-auth/custom-auth/mcp-server-auth-dialog.tsx`)
- 중복 상수 단일화: `CUSTOM_CONNECTION_PROVIDER_NAME`, `PREBUILT_PROVIDER_I18N_KEY`, `PREBUILT_PROVIDER_NAMES` → `lib/types`
- `useCredential(id)` selector + `ConnectionStatusBadge` 컴포넌트 추출

### 검증
ruff PASS · **pytest 646 passed** (변경 0, 회귀 0) · pnpm lint(기존 1건만) · pnpm build 15 routes · F 흡수 grep 0

### 리뷰 이력
사티아 내부 + codex review 3회차 (P1×3, P2×5 해소) + codex adversarial 2회차 (high 2건 해소) + /simplify 3 agent (재사용/품질/효율 6+3건 정리)

## 다음 작업

### 사용자 직접
1. 브라우저 E2E 11항목 (`tasks/manual-e2e-e-m5.md §3`)
2. staged → 단일 커밋 → push → PR 생성

### 이후 마일스톤 — **M6: Cleanup**
**스코프**:
- Alembic `m12_drop_legacy_columns`: `mcp_servers` drop, `tool.credential_id/auth_config/mcp_server_id` drop, `agent_tools.config` drop
- legacy fallback 코드 제거 (chat_service custom/mcp fallback 경로, `credential_service.resolve_server_auth`)
- **옵션 D 정공 흡수**: `PATCH /api/tools/{id}`에 `connection_id` 필드 추가 → ConnectionBindingDialog의 bridge/N:1 공유 한계 해소
- UI 후속 리팩토링: `triggerContext` mode 일원화, Body split + `BindingDialogShell` 추출 (M5에서 "M6 cleanup 시"로 미룬 부채들)

### M5.5 — `agent_tools.connection_id` override (별도 PR)
원래 exec-plan M5에 포함이었으나 분리. 백엔드(m12? — M6 이전/이후 배치 재결정) + UI. M6와 배치 순서는 사용자 결정.

## 주의사항 / invariant (M6 재사용)

### 정책
- CUSTOM runtime kill-switch(`status='active'`) > bridge override 선행
- PREBUILT "no connection" → env fallback / CUSTOM "no connection" → legacy fallback (비대칭 의도)
- CUSTOM `tool.credential_id` = `connection.credential_id` derive (M6에서 credential_id 컬럼 drop 시 자동 해소)
- PREBUILT usage/삭제 가드는 `is_default` connection만 카운트

### M5에서 남긴 의식된 부채 (M6에서 해소)
1. **MCP N:1 공유 rebind**: 1:1만 safe sync, N:1은 차단+안내. 정공=옵션 D (새 connection find-or-create + tool.connection_id PATCH)
2. **Custom legacy first-bind**: `tool.connection_id` 미저장, credential_id만 동기화. 정공=옵션 D
3. `triggerContext` string union + prop 비대칭(`connectionId` vs `currentConnectionId`) → 옵션 D 적용 시 mode 재정의
4. ConnectionBindingDialog 590줄 단일 파일 → Body split은 옵션 D 적용 시 같이

### drive-by 금지
- `use-connections.ts`의 `['connections']` prefix invalidate는 "is_default 승격" 의식적 결정. 좁히기 전 분석+테스트 필요
- `use-chat-runtime.ts:74` streamError unused warning은 기존 부채, M5에서 건드리지 말 것 유지

## 마일스톤 진행
| M0 | M1 | M2 | M3 | M4 | M5 | M5.5 | M6 |
|---|---|---|---|---|---|---|---|
| PR #52 | PR #53 | PR #54 | PR #55 | PR #56 | **머지 대기** | 분리 | 다음 |

## 마지막 상태
- 브랜치: `feature/backlog-e-m5` (PR 미생성, user 진행)
- Base: main @ `12d3d18`
- DB head: `m11_custom_connection` (M4 from M5 무변경)
- TTH 팀: `backlog-e-m5` 해산 완료
- 보존 worktree: `backlog-e-m1~m5`
