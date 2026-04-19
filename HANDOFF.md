# 작업 인계 문서

## 최근 완료 (2026-04-19)

**PR #56 제출 — 백로그 E M4: CUSTOM Connection 통합**
https://github.com/YooSuhwa/natural-mold/pull/56 · worktree `.claude/worktrees/backlog-e-m4` / 브랜치 `feature/custom-connection-migration` / base main@`44a39c6`

### 핵심 변경
- Alembic `m11_custom_connection`: `(user_id, credential_id)` dedup 백필 + `[m11-auto-seed]` 마커 + partial unique index `uq_connections_custom_one_per_credential`. 3종 provenance 테이블(`_m11_tool_backfill_provenance` / `_m11_dedup_connection_snapshot` / `_m11_dedup_tool_remap`)로 downgrade 완전 reversible
- `chat_service._resolve_custom_auth` 신규 — kill-switch(status='active') 선행, 그 다음 bridge override(`tool.credential_id != conn.credential_id` → legacy)
- `_gate_connection_active` / `_gate_connection_credential` 공용 헬퍼 — PREBUILT/CUSTOM 공유
- `connection_service.validate_connection_for_custom_tool` + server-side get-or-create (disabled 409)
- `tool_service.create_custom_tool` — `connection_id`를 SOT로, `credential_id`는 서버 derive (split-brain 차단)
- 에러 코드 `CUSTOM_CONNECTION_DISABLED` / `CUSTOM_CONNECTION_UNBOUND` 모듈 상수화
- Frontend: `add-tool-dialog` Custom 탭 find-or-create + `useCreateConnection.onSuccess` setQueryData seed

### 검증
ruff PASS · **pytest 646 passed** (+32 신규, 회귀 0) · Alembic PG 왕복 PASS · pnpm lint 0 errors · pnpm build 15 routes

### 리뷰 이력 (5 라운드 반영)
Codex adversarial 1~4차 + /simplify 3-agent. 주요 수정: kill-switch 선행 · N:1 server enforcement · downgrade reversibility · canonical active-first · split-brain 차단 · `_resolve_*_auth` 공용 gate 추출.

## 다음 작업 — **M5: UI 통합 + F 흡수**

**ADR**: `docs/design-docs/adr-008-connection-entity.md`
**계획**: `docs/exec-plans/active/backlog-e-connection-refactor.md` (§4 M5)

스코프:
- `custom-auth-dialog.tsx` → `ConnectionBindingDialog(type='custom')` 교체 (M3 PREBUILT 패턴)
- `mcp-server-auth-dialog.tsx` → `ConnectionBindingDialog(type='mcp')` 교체
- `add-tool-dialog.tsx` MCP 탭 재배선 (Custom 탭은 M4 완료)
- `/connections` 페이지 재편: CUSTOM + MCP 섹션 추가
- `agent_tools.connection_id` override UI (에이전트 설정)
- 3 dialog 중복 제거 → F 완료 처리

### 새 세션 진입
```
PR #56 머지 확인 → /sync → 새 worktree backlog-e-m5 → docs/exec-plans/active/backlog-e-connection-refactor.md (M5) + adr-008 읽고 M5 시작
```

## 마일스톤 진행
| M0 | M1 | M2 | M3 | M4 | M5 | M6 |
|---|---|---|---|---|---|---|
| PR #52 | PR #53 | PR #54 | PR #55 | **PR #56** | 다음 | |

## 주의사항 (M5+ 재사용)

### 구조 invariant
- 모델 컬럼 추가 시 Pydantic 응답/요청 스키마 + FE 타입을 **세트**로 체크 (M3 `provider_name` / M4 `connection_id` 두 번 다 S4 블로커)
- Alembic revision ID는 **32자 이하** (alembic_version VARCHAR(32))
- `is_default` partial unique index는 user×type×provider당 1개 — 시드/이관 시 첫 번째만 true, 나머지 false
- PG 네이티브 SQL 이관은 aiosqlite 테스트에서 `inspect.getsource` 계약 가드로만 검증 (실제 왕복은 PG)
- `uq_connections_custom_one_per_credential` partial unique index — CUSTOM N:1 invariant DB 강제

### 정책 invariant (chat_service)
- CUSTOM runtime은 kill-switch(status='active')가 bridge override보다 **반드시 선행** — rotation이 disabled connection을 우회 불가
- PREBUILT "connection 없음" → env fallback(`{}`), CUSTOM "connection 없음" → legacy fallback. 비대칭 의도
- CUSTOM `tool.credential_id`는 `connection.credential_id`에서 derive. `custom-auth-dialog` PATCH `/auth-config`만이 두 값을 divergence시키고, 그때 bridge override 경로가 활성화 (M6에서 제거)
- 공통 방어 함수 에러 메시지는 도구 타입 중립("Tool '{name}'"). incident triage 오분류 방지

### 파일 경계 / drive-by 금지
- S1 삭제 분석에서 "보류 M6" 결정한 항목은 당해 M PR에서 절대 건드리지 않음
- M4 scope out 실제로 M4에서 유지: `custom-auth-dialog.tsx`, `mcp-server-auth-dialog.tsx`, `/connections` CUSTOM/MCP 섹션

### 알려진 pre-existing 이슈
- `frontend/src/lib/chat/use-chat-runtime.ts:74` `streamError` unused (lint warning)
- 깨진 프론트 테스트: `tests/components/chat/*`, `tests/pages/chat.test.tsx`, `agent-*`

## 마지막 상태

- 브랜치: `feature/custom-connection-migration` (PR #56, 머지 대기)
- Base: main @ `44a39c6` (PR #55 머지 — M3)
- 커밋: `36b2520 [feat] 백로그 E M4 — CUSTOM Connection 통합`
- DB head: `m11_custom_connection`
- TTH 팀: `backlog-e-m4` 해산 완료
- 보존 worktree: `backlog-e-m1`, `backlog-e-m2`, `backlog-e-m3`, `backlog-e-m4` (M4는 PR 머지 후 정리)
