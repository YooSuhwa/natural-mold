# CHECKPOINT — 백로그 E M5 · UI 통합 + F 흡수 (프론트 전용)

**브랜치**: `feature/backlog-e-m5`
**worktree**: `/Users/chester/dev/natural-mold/.claude/worktrees/backlog-e-m5`
**base**: main @ `12d3d18` (PR #57 머지 — M4 HANDOFF docs 후속)
**ADR**: `docs/design-docs/adr-008-connection-entity.md`
**실행계획**: `docs/exec-plans/active/backlog-e-connection-refactor.md` (§4 M5)
**팀**: 팀쿡(UX 스펙 + 디자인 리뷰) + 저커버그(구현 DRI) + 베조스(삭제 분석 + 회귀) — 사티아 리드

---

## 스코프 합의 (2026-04-19, 사용자 승인)

| 항목 | 결정 |
|------|------|
| 스코프 | M5 = **프론트엔드 전용** (5개 항목). `agent_tools.connection_id` override는 M5.5로 분리 |
| /connections 재편 깊이 | **Connection 중심 전면 재편** — Credential 카드 제거, Connection이 1급. Credential은 Connection 상세 안에서만 노출 |
| 백엔드 변경 | **없음** — M4까지 백엔드 완료. 신규 alembic/모델/서비스 변경 0 |
| Legacy fallback | M6까지 유지 — `tool.credential_id IS NULL AND tool.auth_config` 있으면 inline auth |
| F(중복 다이얼로그 흡수) | M5에서 완료 — 3개 dialog → ConnectionBindingDialog 1개로 수렴 |
| 향후 M5.5 | `agent_tools.connection_id` override (백엔드 m12 + chat_service + UI). M5 머지 후 별도 worktree |
| 향후 M6 | `tool.credential_id` / `tool.auth_config` / `tool.mcp_server_id` / `agent_tools.config` drop + legacy 코드 제거 |

---

## S0: docs/ 구조 확인 [done]

- [x] main에 docs/, ADR-008, exec-plan 존재
- [x] M4 progress.txt / CHECKPOINT.md를 tasks/archive/로 이동
- 검증: `ls tasks/archive/progress-backlog-e-m4.txt tasks/archive/CHECKPOINT-backlog-e-m4.md`

## S1: 삭제 분석 (베조스) [blockedBy: S0]

- [ ] M5 스코프 legacy 코드 식별:
  - `components/tool/prebuilt-auth-dialog.tsx`, `custom-auth-dialog.tsx`, `mcp-server-auth-dialog.tsx` — 3종 다이얼로그 중복 표면 분석 (제거/대체/유지 분류)
  - `app/connections/page.tsx` 현재 구조 — Credential 카드, PREBUILT 섹션 의존성 분석
  - `add-tool-dialog.tsx` MCP 탭 현 동작 (mcp_server_id 직접 선택) → connection 기반 재배선 영역
  - `lib/api/credentials.ts` / `lib/hooks/use-credentials.ts` — /connections 재편 시 사용량 변화
- [ ] `tasks/deletion-analysis-e-m5.md` (즉시 삭제 / 단순화 / 보류 M6 이월)
- [ ] **반드시 분리**: M5.5(agent_tools.override)/M6(legacy drop)에서 처리할 항목은 명시적으로 "보류"로 표시
- 검증: 보고서 존재, drive-by 금지 준수, 백엔드 파일 0건

## S2: ConnectionBindingDialog 셸 + UX 스펙 (팀쿡) [blockedBy: S0]

- [ ] `docs/design-docs/m5-connection-binding-dialog-spec.md` 작성
  - 3 dialog(prebuilt/custom/mcp) 공통 surface 정의
  - props 계약: `type: 'prebuilt' | 'custom' | 'mcp'`, `provider_name`, `tool` (or external context), `onBound`
  - 상태 머신: idle → loading → connection_select(기존 active connection 목록) → credential_form(새로 만들 때) → binding → success/error
  - PREBUILT vs CUSTOM vs MCP 차이점 명시 (provider_name 고정 vs 선택 vs server config 입력)
  - i18n 키 네이밍: `connection.binding.{type}.*`
  - 접근성: focus trap, ESC 닫기, 에러 알림(role=alert)
- [ ] `docs/design-docs/m5-connections-page-redesign-spec.md` 작성
  - Credential 카드 제거 → Connection 카드 1급
  - 섹션: PREBUILT (provider별 그룹) / CUSTOM / MCP
  - Connection 상세 (drawer or modal): credential 메타, 사용 중 tool 목록, status toggle, 삭제
  - "연결 추가" CTA → ConnectionBindingDialog 진입
  - 빈 상태 카피, 에러 표시 정책
- 검증: 두 spec 문서 존재, 저커버그가 spec 읽고 구현 가능 (소프트 게이트)

## S3: ConnectionBindingDialog 구현 + 3 dialog 교체 (저커버그) [blockedBy: S1, S2]

- [ ] `frontend/src/components/connection/ConnectionBindingDialog.tsx` 신규 — 공통 셸
  - Props: `type`, `provider_name?`, `triggerContext` (tool 생성 / tool 편집 / standalone), `onBound(connection)`
  - 내부 상태: `useConnections({type, provider_name})` 조회 → 기존 connection 선택 or 신규 생성 분기
  - 신규 생성 시: CredentialFormDialog → `useCreateConnection` → setQueryData seed (M4 패턴 재사용)
  - PREBUILT 모드: provider_name 고정, credential form만 제공
  - CUSTOM 모드: M4 add-tool-dialog Custom 탭 패턴 흡수
  - MCP 모드: server config(name, transport, url, headers) + credential 옵션
- [ ] `prebuilt-auth-dialog.tsx` → `ConnectionBindingDialog(type='prebuilt')`로 호출 변경. 기존 파일은 thin wrapper or 제거
- [ ] `custom-auth-dialog.tsx` → `ConnectionBindingDialog(type='custom')` 교체. M4 bridge override 흐름(`tool.credential_id != connection.credential_id`)은 절대 건드리지 않음 — M6에서 정리
- [ ] `mcp-server-auth-dialog.tsx` → `ConnectionBindingDialog(type='mcp')` 교체
- [ ] `add-tool-dialog.tsx` MCP 탭 재배선 — server 직접 선택 → ConnectionBindingDialog 진입 (Custom 탭은 M4 완료, 변경 없음)
- [ ] i18n `messages/ko.json` `connection.binding.*` 키 추가
- [ ] **F 흡수 검증**: 3 dialog 파일이 thin wrapper(or 제거)가 되었는지 grep로 확인 (`rg "Auth.*Dialog" components/tool/`)
- 검증: pnpm lint PASS, pnpm build PASS, 기존 화면(에이전트 도구 탭/추가) 수동 회귀 OK

## S4: /connections 페이지 Connection 중심 재편 (저커버그) [blockedBy: S2]

- [ ] `app/connections/page.tsx` 재구조화
  - 기존 Credential 카드 섹션 제거 (Credential 직접 노출 → Connection 안으로 흡수)
  - 섹션 순서: PREBUILT (provider별 그룹) → CUSTOM → MCP
  - 각 섹션 헤더 + "연결 추가" CTA → ConnectionBindingDialog 진입
  - Connection 카드: name, status, provider, 사용 중 tool 카운트
- [ ] Connection 상세 패널 (drawer 권장, 모달도 가능)
  - credential 메타 (이름, 마스킹된 일부)
  - 사용 중 tool 목록
  - status toggle (active ↔ disabled), 삭제 (사용 중 tool 있으면 차단/경고)
  - PATCH `/api/connections/{id}` 호출 (M1에서 구현됨)
- [ ] Credential 단독 관리 UI 제거 (`lib/hooks/use-credentials.ts`는 backend 호환 위해 유지, UI에서만 분리)
- [ ] 빈 상태: "아직 연결이 없습니다" 카피 + CTA
- 검증: pnpm lint PASS, pnpm build PASS, 모든 connection CRUD 수동 검증, 기존 PREBUILT 흐름(M3 connections 페이지) 회귀 0

## S5: 회귀 검증 + 신규 컴포넌트 테스트 (베조스) [blockedBy: S3, S4]

- [ ] **백엔드 테스트 변경 없음** (스코프 백엔드 0). 단, 회귀 확인 위해 `uv run pytest` 1회 실행 — 646 유지
- [ ] 프론트 깨진 테스트(HANDOFF 알려진 이슈)는 M5에서 신규로 깨뜨리지만 않으면 OK. 추가 깨짐 0 확인
- [ ] 수동 E2E 시나리오 (베조스 작성 → 사티아 검토)
  - PREBUILT: connections 페이지 → Naver provider 연결 추가 → tool 생성 시 자동 매칭
  - CUSTOM: tool 생성 → Custom 탭 → 신규 credential → connection 자동 생성
  - MCP: tool 생성 → MCP 탭 → 새 server connection → tool 등록
  - Connection 비활성화 → tool 호출 시 disabled 에러 (M3/M4 fail-closed 회귀 확인)
- [ ] `tasks/manual-e2e-e-m5.md` 시나리오 + 결과 기록
- 검증: pytest 646+ 유지, pnpm lint 0 errors, pnpm build PASS, 수동 E2E 보고서 존재

## S6: 통합 + 커밋 (사티아) [blockedBy: S5]

- [ ] 전체 verify: ruff + pytest + pnpm lint + pnpm build
- [ ] /codex:review (대규모 UI 변경 — 권장)
- [ ] HANDOFF.md 업데이트 (M5 완료, 다음 = M5.5 또는 M6)
- [ ] 단일 커밋 → PR

---

## 리스크 (M5 포인트)

1. **3 dialog 흡수 시 props 표면 충돌** — PREBUILT는 provider 고정, CUSTOM은 자유, MCP는 server config 추가. 단일 셸이 비대해질 수 있음. 팀쿡 spec에서 type별 sub-section 명확히
2. **/connections 전면 재편 회귀** — M3에서 PREBUILT 섹션이 이미 동작 중. 사용자가 만든 connection들이 새 UI에서 모두 보이는지 manual E2E 필수
3. **add-tool-dialog MCP 탭 재배선** — 기존 mcp_server_id 직접 선택 흐름. 새 connection 기반 흐름과의 마이그레이션 UX 주의 (이미 등록된 mcp_server는 어떻게 보이는가)
4. **i18n 키 충돌** — `connection.binding.*` 신규 키. 기존 `tool.addDialog.*` 키와 중복 금지
5. **Credential 카드 제거** — credentials API 자체는 살아 있어야 함 (Connection 안에서 호출). UI 표면만 제거
6. **drive-by 금지 (M5.5/M6)** — `agent_tools.connection_id` 백엔드 변경 0건, `tool.credential_id` 컬럼 drop 0건. 베조스 S1에서 명시적으로 "보류" 표시

---

## 검증 커맨드

```bash
cd backend
uv run ruff check .
uv run pytest                           # 646+ 유지

cd ../frontend
pnpm lint
pnpm build

# F 흡수 검증
rg -l "AuthDialog" frontend/src/components/tool/ | wc -l   # 0 또는 thin wrapper만
rg -l "ConnectionBindingDialog" frontend/src/components/   # 신규 셸 + 호출처 N건
```
