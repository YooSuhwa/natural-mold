# CHECKPOINT — 백로그 E M4 · CUSTOM Connection 통합

**브랜치**: `feature/custom-connection-migration`
**worktree**: `/Users/chester/dev/natural-mold/.claude/worktrees/backlog-e-m4`
**base**: main @ `44a39c6` (PR #55 머지 — M3 완료)
**ADR**: `docs/design-docs/adr-008-connection-entity.md`
**실행계획**: `docs/exec-plans/active/backlog-e-connection-refactor.md` (§4 M4)
**팀**: 피차이(Alembic m11 + 모델) + 젠슨(chat_service CUSTOM 분기 + connection_service CUSTOM 헬퍼) + 저커버그(add-tool-dialog Custom 탭 재배선) + 베조스(삭제 분석 + 회귀 + 신규 테스트) — 사티아 리드

---

## 스코프 합의 (2026-04-18)

| 항목 | 결정 |
|------|------|
| 스코프 | exec-plan §4 M4 그대로 (백엔드 + add-tool-dialog Custom 탭만) |
| Legacy fallback | M6까지 유지 — `tool.connection_id IS NULL AND tool.credential_id` 있으면 기존 경로 |
| M5 범위(custom-auth-dialog 교체 / agent_tools.connection_id override) | 이월 — M4에서 당기지 않음 |
| CUSTOM Connection 형태 | `type='custom'`, `provider_name='custom_api_key'` (credential_registry), 1 credential = 1 connection, N tools → 1 connection 공유 가능 |
| 이관 정책(m11) | 기존 `tools.credential_id IS NOT NULL AND type='custom'` row마다 idempotent connection 생성 + `tool.connection_id` FK 설정. credential 1개를 공유하는 여러 tool은 동일 connection 재사용 |

---

## S0: docs/ 구조 확인 [done]

- [x] main에 docs/, ADR-008, exec-plan 존재
- [x] M3 progress.txt / CHECKPOINT.md를 tasks/archive/로 이동
- 검증: `ls tasks/archive/progress-backlog-e-m3.txt tasks/archive/checkpoint-backlog-e-m3.md`

## S1: 삭제 분석 (베조스) [blockedBy: S0]

- [ ] M4 스코프 legacy 코드 식별: CUSTOM에서 `tool.credential_id` 경유 경로, `add-tool-dialog` custom tab의 credential 바인딩, `tools` 라우터에서 CUSTOM credential update 경로
- [ ] `tasks/deletion-analysis-e-m4.md` (즉시 삭제 / 단순화 / 보류 M6 이월)
- 검증: 보고서 존재, drive-by 금지 준수

## S2: Alembic m11 + CUSTOM connection backfill (피차이) [blockedBy: S0]

- [ ] `backend/alembic/versions/m11_custom_credential_migration.py`
  - revision ID: `m11_custom_connection` (32자 이하, 축약 — M3 PG VARCHAR(32) 학습)
  - `down_revision = "m10_prebuilt_connection"`
  - upgrade: CUSTOM 도구 이관 백필
    - 대상: `tools.type = 'custom' AND tools.credential_id IS NOT NULL AND tools.connection_id IS NULL`
    - 각 (user_id, credential_id) 튜플마다 1개 connection 생성 (`type='custom'`, `provider_name='custom_api_key'`, `display_name=credential.name`, `is_default=true`, `status='active'`, `credential_id` FK 설정)
    - 같은 credential을 참조하는 여러 CUSTOM tool은 동일 connection을 공유 (idempotent: 이미 `(user_id, type='custom', credential_id)` connection이 존재하면 재사용)
    - 해당 tool rows의 `tool.connection_id` FK 설정
    - `M11_SEED_MARKER = "[m11-auto-seed]"` 프리픽스를 display_name에 박아 downgrade 식별
  - downgrade: `[m11-auto-seed]` 마커로 식별된 connection만 삭제 (수동 생성분 보호) + 해당 tool의 connection_id FK 해제
  - **주의**: `tool.credential_id`는 drop하지 않음 (M6까지 legacy fallback). `tool.auth_config` 또한 유지
- [ ] **수정 없음**: `app/models/tool.py` `connection_id`/`connection` 이미 M2에서 추가됨 — 확인만
- 검증: alembic 왕복 PASS, pytest 614+ 회귀 0, idempotent 재실행 검증

## S3: chat_service CUSTOM 분기 재작성 + connection_service CUSTOM 헬퍼 (젠슨) [blockedBy: S2]

- [ ] `backend/app/services/chat_service.py:393-396` CUSTOM else 분기 재작성
  - 신규 우선순위: `tool.type == CUSTOM`
    1. `tool.connection_id IS NOT NULL AND tool.connection IS NOT NULL` → ownership 가드 (`assert_connection_ownership` + `assert_credential_ownership`) → credential 복호화. `tool.connection.status != 'active'` 또는 `credential IS NULL` → `ToolConfigError` (PREBUILT M3와 동일 fail-closed 정책)
    2. Legacy fallback: `tool.connection_id IS NULL` → `_resolve_legacy_tool_auth(tool)` (현 경로 유지) — M6까지 tolerance
  - 신규 모듈-private 헬퍼: `_resolve_custom_auth(tool) -> dict[str, Any]` (M3의 `_resolve_prebuilt_auth` 패턴과 대칭)
- [ ] `backend/app/services/connection_service.py` — CUSTOM 전용 헬퍼가 필요하면 추가 (PREBUILT bulk 헬퍼는 재사용 불가 — CUSTOM은 tool 단위 FK라 `selectinload(Tool.connection).selectinload(Connection.credential)`로 이미 해결됨. 추가 헬퍼는 **불필요할 가능성 높음**. 젠슨이 판단)
- [ ] `get_agent_with_tools`의 `selectinload(Tool.connection).selectinload(Connection.credential)` 체인은 M2에서 이미 걸려 있음 — **수정 없음**
- 검증: ruff PASS, pytest 614+ 회귀 0

## S4: 프론트엔드 add-tool-dialog Custom 탭 재배선 (저커버그) [blockedBy: S2]

- [ ] `frontend/src/components/tool/add-tool-dialog.tsx` Custom 탭
  - 현 동작: user가 credential을 직접 선택 → `tool.credential_id`로 저장
  - 신규 동작: user가 credential 선택 → 없으면 `CredentialFormDialog`로 생성 → 해당 credential에 바인딩된 CUSTOM connection을 find-or-create (`useConnections({type:'custom', provider_name:'custom_api_key'})` + credential_id로 필터) → tool 생성 시 `connection_id` 포함
  - Legacy fallback 호환: 기존 `credential_id` 기반 tool은 그대로 표시 (M6 drop까지)
- [ ] `frontend/src/lib/api/tools.ts` / `frontend/src/lib/types/index.ts` — `Tool.connection_id` 이미 M2에서 추가됨. `ToolCreateRequest`에 `connection_id?` 전달 필드 확인 + 누락 시 추가
- [ ] i18n `messages/ko.json` — `tool.addDialog.custom.*` 메시지 보강 (연결 생성 UX)
- [ ] **scope out**: `custom-auth-dialog.tsx` 교체(M5), `mcp-server-auth-dialog.tsx` 교체(M5), `/connections` 페이지 CUSTOM 섹션(M5). S1 분석서 "drive-by 금지" 원칙 유지
- 검증: pnpm lint PASS, pnpm build PASS

## S5: 회귀 + 신규 테스트 (베조스) [blockedBy: S3, S4]

- [ ] `tests/test_connection_custom_resolve.py` 신규
  - user_A/B 격리: 같은 CUSTOM tool 정의에서 각자의 connection.credential로 분기 (CUSTOM은 공유 행이 아니라 tool 단위라 per-tool user_id 격리 검증)
  - connection_id 있고 active + credential 있음 → 정상 복호화
  - connection_id 있고 status='disabled' → `ToolConfigError`
  - connection_id 있고 credential=NULL → `ToolConfigError`
  - connection_id=NULL (legacy) + credential_id → 기존 경로 유지
  - connection_id=NULL + credential_id=NULL + auth_config → inline auth 반환
  - ownership mismatch (connection.user_id ≠ credential.user_id) → `ToolConfigError`
- [ ] `tests/test_tools_router_extended.py` — CUSTOM tool response에 `connection_id` 필드 회귀 검증
- [ ] Alembic m11 idempotent + downgrade 가드 (M9/M10 precedent: `inspect.getsource` helper 소스 계약)
- 검증: `uv run pytest` 614+ 유지 + 신규 PASS

## S6: 통합 + 커밋 (사티아) [blockedBy: S5]

- [ ] 전체 verify: ruff + pytest + alembic 왕복 + pnpm lint + pnpm build
- [ ] /codex:review
- [ ] HANDOFF.md 업데이트 (M4 완료, 다음 = M5)
- [ ] 단일 커밋 → PR

---

## 리스크 (M4 포인트)

1. **credential 공유 → connection 1개** — 같은 credential을 여러 CUSTOM tool이 참조하는 경우, N connection이 아닌 1 connection을 공유해야 한다. m11은 `(user_id, credential_id)` 단위 dedup 필수.
2. **CUSTOM `user_id`가 있는 row** — CUSTOM tool은 `user_id NOT NULL` (PREBUILT처럼 `is_system=True` 공유 행이 아님). ownership 가드는 실질 동작.
3. **Legacy fallback 경로 회귀** — `tool.connection_id IS NULL AND credential_id IS NOT NULL` 시나리오는 M3 이전 생성된 CUSTOM tool에 해당. 테스트 필수.
4. **add-tool-dialog Custom 탭 UX** — find-or-create 패턴은 React Query invalidation 타이밍 + optimistic update 없음 기준. 저커버그 M3 패턴 재사용.
5. **m11 revision ID 32자 제한** — PG alembic_version VARCHAR(32). `m11_custom_connection`(21자) OK.
6. **M5 drive-by 금지** — custom-auth-dialog.tsx / mcp-server-auth-dialog.tsx / `/connections` 페이지는 절대 건드리지 않는다.

---

## 검증 커맨드

```bash
cd backend
uv run ruff check .
uv run pytest tests/test_connection_custom_resolve.py -v
uv run pytest                           # 614+ 유지
uv run alembic upgrade head
uv run alembic downgrade -1
uv run alembic upgrade head             # 왕복 PASS

cd ../frontend
pnpm lint
pnpm build
```
