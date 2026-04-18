# CHECKPOINT — 백로그 E M3 · PREBUILT per-user Connection

**브랜치**: `feature/prebuilt-per-user-connection`
**worktree**: `/Users/chester/dev/natural-mold/.claude/worktrees/backlog-e-m3`
**base**: main @ `b34125d` (PR #54 머지 — M2 완료)
**ADR**: `docs/design-docs/adr-008-connection-entity.md`
**실행계획**: `docs/exec-plans/active/backlog-e-connection-refactor.md` (§4 M3)
**팀**: 피차이(Alembic m10 + 모델) + 젠슨(chat_service PREBUILT 분기) + 저커버그(프론트엔드 dialog 재배선) + 베조스(회귀 + QA) — 사티아 리드

---

## 스코프 합의 (2026-04-18)

| 항목 | 결정 |
|------|------|
| 스코프 | Backend + 전체 dialog 재배선 (M5 일부 당김) |
| env fallback | 유지 (ADR-008 §11) — connection 없을 때 settings.* 값 사용 |
| mock user 시드 | Alembic m10 데이터 마이그레이션에서 자동 시드 |
| Tool-Provider 매핑 | `tools.provider_name` 컬럼 신설 (PREBUILT만 NOT NULL, 그 외 NULL) |
| `tools.credential_id` | PREBUILT에서 무시 (legacy fallback은 M6까지 유지) |

---

## S0: docs/ 구조 확인 [done]

- [x] main에 docs/, ADR-008, exec-plan 존재
- 검증: `ls docs/design-docs/adr-008-connection-entity.md docs/exec-plans/active/backlog-e-connection-refactor.md`

## S1: 삭제 분석 (베조스) [blockedBy: S0]

- [ ] M3 스코프 legacy 코드 식별: `tools.credential_id` PREBUILT 경로, 3개 auth-dialog 중복
- [ ] `tasks/deletion-analysis-e-m3.md` 보고서 (즉시 삭제 / 단순화 / 보류 M6 이월)
- 검증: 보고서 존재, drive-by 금지 준수

## S2: Alembic m10 + tools.provider_name + Connection seed (피차이) [blockedBy: S0]

- [ ] `backend/alembic/versions/m10_prebuilt_connection_migration.py`
  - upgrade: `tools.provider_name` VARCHAR(50) nullable 컬럼 추가
  - upgrade: 기존 PREBUILT tools의 name → provider_name 매핑 백필
    - `Naver *` → `naver`
    - `Google Search`, `Google Image`, `Google News` → `google_search`
    - `Gmail *`, `Google Calendar *` → `google_workspace`
    - `Google Chat *` → `google_chat`
  - upgrade: mock user env 값(settings.naver_*, google_*) → credential → default connection 자동 시드
    - 이미 동일 provider의 credential+default connection이 있으면 skip (idempotent)
    - env 값이 비어있으면 시드 skip
  - downgrade: 마이그레이션으로 생성된 connections(is_default=true AND user_id=mock) + credentials 역삭제, provider_name 컬럼 drop
- [ ] `backend/app/models/tool.py` — `provider_name` Mapped 컬럼 추가
- [ ] `backend/app/seed/default_tools.py` — 신규 PREBUILT tool 시드에 `provider_name` 포함
- 검증: alembic 왕복 PASS, pytest 585+ 회귀 0, 신규 mock user 시드 검증

## S3: chat_service PREBUILT 분기 + connection_service helper (젠슨) [blockedBy: S2]

- [ ] `backend/app/services/chat_service.py:254-260` PREBUILT 분기 재작성
  - 신규: `tool.type == PREBUILT AND tool.provider_name` → connection 조회 헬퍼
    - default connection 우선 (user_id + type='prebuilt' + provider_name + is_default=true)
    - 있으면: `resolve_credential_data(conn.credential)` → cred_auth
    - 없으면: env fallback (`cred_auth = {}` → 기존 settings.* 경로 유지)
  - Legacy fallback: `tool.type == PREBUILT AND tool.provider_name IS NULL` → 기존 credential_id 경로 (이행 tolerance)
  - CUSTOM 분기는 기존 `tool.credential_id` 경로 그대로 (M4 대상)
- [ ] `backend/app/services/connection_service.py` — `get_default_connection(db, user_id, type, provider_name)` 헬퍼 추가 (sync select → selectinload(credential))
- [ ] `get_agent_with_tools`에 user default connection 프리로드 확장 (N+1 방지)
- [ ] Cross-tenant 가드: 이미 구현된 `assert_connection_ownership` / `assert_credential_ownership` 재사용
- 검증: ruff PASS, 기존 PREBUILT 테스트 회귀 0

## S4: 프론트엔드 dialog 재배선 + /connections PREBUILT UI (저커버그) [blockedBy: S2] — DONE

- [x] `frontend/src/components/connection/connection-binding-dialog.tsx` 공통 셸 신설 — `{type:'prebuilt', providerName, toolName?, open, onOpenChange, onSaved?}` props. `useConnections({type, provider_name})` → 기존 default connection 있으면 PATCH, 없으면 POST(is_default=true). CredentialSelect + CredentialFormDialog 재사용. React 19 "render 중 setState + guard" 패턴으로 하이드레이션.
- [x] `frontend/src/components/tool/prebuilt-auth-dialog.tsx` → 얇은 어댑터로 축소. detectProvider 휴리스틱 삭제, useUpdateToolAuthConfig 호출 0. `tool.provider_name` 사용. null일 때 TooltipProvider+disabled trigger로 `legacyUnavailable` 안내.
- [x] `frontend/src/app/connections/page.tsx` — PrebuiltConnectionSection 추가(기존 Credential 리스트 유지). 4개 provider 카드 + 기본 연결 표시 + "연결 추가" 버튼 → ConnectionBindingDialog.
- [x] `frontend/src/lib/api/connections.ts` 신설(M1 미존재 확인), `frontend/src/lib/hooks/use-connections.ts` 신설(scope-wide invalidation). `frontend/src/lib/types/index.ts`에 `Tool.provider_name`, ConnectionType/Status/McpAuthType/McpTransport, Connection, ConnectionCreateRequest, ConnectionUpdateRequest 추가.
- [x] `messages/ko.json` — `connections.bindingDialog.*`, `connections.prebuiltSection.*`, `tool.authDialog.legacyUnavailable` 추가.
- [~] custom-auth-dialog / mcp-server-auth-dialog — S1 분석서 "drive-by 금지" 원칙 따라 scope out. M4(custom)/M5(mcp) 이월.
- 검증: pnpm lint PASS (0 errors, pre-existing streamError warning만), pnpm build PASS (타입체크 + 14 static routes).
- 블로커(S5/S6 사전 필요): `ToolResponse` 스키마에 `provider_name: str | None = None` 추가 — `backend/app/schemas/tool.py:64`. 모델에는 있으나 Pydantic 직렬화 누락 → 런타임 tool.provider_name=undefined → PrebuiltAuthDialog 전원 legacyUnavailable로 떨어짐. 1줄 핫픽스 필요.

## S5: 회귀 + 신규 테스트 (베조스) [blockedBy: S3, S4]

- [ ] `tests/test_connection_prebuilt_resolve.py` 신규
  - mock user 2개로 서로 다른 connection을 갖고 같은 PREBUILT tool 실행 → 각자 credential 적용 검증
  - default connection 없을 때 env fallback 유지 검증
  - legacy fallback(provider_name IS NULL) 검증
- [ ] `tests/test_tools_router_extended.py`, `tests/test_naver_tool.py` 등 기존 PREBUILT 테스트 회귀 갱신
- [ ] Alembic m10 데이터 무결성 테스트 (mock user 시드 idempotent)
- 검증: `uv run pytest` 585+ 유지 + 신규 PASS

## S6: 통합 + 커밋 (사티아) [blockedBy: S5]

- [ ] 전체 verify: ruff + pytest + alembic 왕복 + pnpm build + /codex:review
- [ ] HANDOFF.md 업데이트
- [ ] 단일 커밋 → PR

---

## 리스크 (M3 포인트)

1. **tool.provider_name 백필 누락** — 기존 PREBUILT tool 중 name 패턴이 예상 밖이면 NULL로 남아 legacy fallback만 동작. 해결: 마이그레이션에서 매핑 실패 row를 WARN 로그로 출력 + 테스트에서 모든 seed tool이 provider_name 보유 검증.
2. **mock user 시드 경합** — 마이그레이션 중 같은 provider에 이미 connection이 있으면 skip. upsert가 아닌 "존재 여부 체크 + insert only" 패턴.
3. **env fallback 경로 회귀** — connection이 없으면 `cred_auth = {}`가 되어 기존 `settings.naver_*` 패턴(build_naver_search_tool 내부)이 작동해야 함. 통합 테스트 필수.
4. **Frontend ConnectionBindingDialog 통합 난이도** — 3 dialog를 한 번에 교체 시 회귀 위험. 병렬 구현 후 한 번에 교체.
5. **CUSTOM은 M3 스코프 밖** — 건드리지 말 것. `tool.credential_id` CUSTOM 경로는 M4에서 이관.
