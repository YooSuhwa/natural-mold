# 삭제 분석 보고서 — 백로그 E M4 · CUSTOM Connection 통합

**담당**: 베조스 (QA/DRI)
**스코프**: exec-plan §4 M4 — `tool.credential_id` → `tool.connection_id` 경로 이관 (CUSTOM만)
**원칙**: drive-by 금지. M6까지 legacy fallback 유지. M5 범위(custom-auth-dialog / mcp-server-auth-dialog / `/connections` 페이지 CUSTOM 섹션)는 건드리지 않음.
**선례**: M3 베조스 분석서(`tasks/archive/progress-backlog-e-m3.txt`의 `[2026-04-18T08:45]` 항목) — PREBUILT는 같은 3분류(즉시삭제 1 / 단순화 6 / 보류 12). M4는 범위가 더 좁다.

---

## TL;DR

- **즉시 삭제 가능: 0건.** M4는 추가 경로(connection)를 기존 경로(credential_id) 위에 얹는 확장 이관 단계다. Legacy fallback(`tool.connection_id IS NULL AND tool.credential_id IS NOT NULL`)을 M6까지 유지하는 게 스코프 합의이므로 CUSTOM에서 떼낼 코드는 없다.
- **단순화: 3건** — S3/S4 구현 시 자연 흡수되는 구조적 제안. 별도 PR로 빼지 않음.
- **보류 (M6 이월): 5건** — `_resolve_legacy_tool_auth`의 CUSTOM 경로, `tool.credential_id`/`tool.auth_config` 컬럼 drop, `PATCH /tools/{id}/auth-config`의 `credential_id` 처리, `ToolCustomCreate.credential_id` 필드, `useUpdateToolAuthConfig` 훅.
- **M5 이월 (현황 기록만): 3건** — `custom-auth-dialog.tsx`, `mcp-server-auth-dialog.tsx`, `/connections` 페이지 CUSTOM 섹션.

---

## 1. 즉시 삭제 가능

**없음.**

**이유**:
- ADR-008 §11 + 스코프 합의(2026-04-18) — `tool.connection_id IS NULL AND tool.credential_id IS NOT NULL` 경로는 M6 cleanup까지 유효해야 한다. M3 진입 이전에 생성된 CUSTOM tool이 이 상태로 남아 있고, m11 backfill이 실패하거나 롤백된 row도 이 경로로 런타임에서 복구된다.
- `tool.auth_config`(inline secret) 경로 역시 CUSTOM 이외의 legacy 데이터를 위해 유지. M4에서 제거 시 기존 유저의 tool 실행이 깨짐.

**함의**:
> M4는 "삭제"가 아니라 **신규 경로 추가 + 우선순위 지정**이다. 실제 제거 작업은 M6에서 일괄 집행.

---

## 2. 단순화 제안

### [S-1] chat_service.py — CUSTOM 분기 대칭 헬퍼 (젠슨 S3에서 구현)

**현재** (`chat_service.py:393-396`):
```python
else:
    # CUSTOM / BUILTIN 등 나머지. M4에서 CUSTOM이 connection 경유로 이관될
    # 때까지 기존 시맨틱 유지 (credential → auth_config → {}).
    cred_auth = _resolve_legacy_tool_auth(tool)
```

**제안**:
- `_resolve_custom_auth(tool) -> dict[str, Any]` 모듈-private 헬퍼 신설 — `_resolve_prebuilt_auth`(M3)와 **대칭 구조**.
- 분기 순서:
  1. `tool.connection_id IS NOT NULL AND tool.connection IS NOT NULL` → ownership 가드 → `conn.status != 'active'` 또는 `conn.credential IS NULL` → `ToolConfigError` (fail-closed) → credential 복호화
  2. `tool.connection_id IS NULL` → `_resolve_legacy_tool_auth(tool)` (M6까지 tolerance)
- PREBUILT와 달리 "connection 없음 = env fallback" 경로가 **없다** — CUSTOM은 env를 안 가짐. 이 시맨틱 차이를 헬퍼 docstring에 반드시 명시.
- `build_tools_config` `elif tool.type == ToolType.CUSTOM:` 분기는 `_resolve_custom_auth(tool)` 1줄로 축약 → CUSTOM/BUILTIN 혼합 `else` 분기에서 CUSTOM을 꺼내 명시적 elif로 승격.

**효과**: `_resolve_legacy_tool_auth`는 M6까지 살아남되 **CUSTOM의 "정상 경로"가 아니라 "이행 tolerance"로 자리가 명확해진다**. M6에서 `_resolve_custom_auth` 내 legacy 분기와 `_resolve_legacy_tool_auth` 자체를 함께 삭제하기 쉽다.

### [S-2] frontend add-tool-dialog.tsx — find-or-create는 dialog 내부에 가두기 (저커버그 S4에서 구현)

**현재** (`add-tool-dialog.tsx:50, 97`):
```tsx
const [customCredentialId, setCustomCredentialId] = useState<string>(CREDENTIAL_NONE)
// ...
...(customCredentialId !== CREDENTIAL_NONE ? { credential_id: customCredentialId } : {}),
```

**제안**:
- `customCredentialId` state는 dialog **내부 상태**로 유지 (UX는 변경 없음 — user는 credential을 고르거나 새로 만든다).
- Submit 시점에 `useConnections({ type: 'custom', provider_name: 'custom_api_key' })`로 해당 credential에 바인딩된 connection을 찾고 없으면 POST — 그 `connection_id`만 tool POST body에 실어 보냄.
- `credential_id`는 body에 **실지 않음** — tool 신규 생성 row는 처음부터 connection-only로 통일. Legacy 경로는 **기존 row만** 커버하도록 격리.
- find-or-create 실패 시 tool 생성도 중단 (orphan connection 방지는 M5 이월 — 스코프 합의).

**효과**: 신규 CUSTOM tool은 m11 backfill 경로를 타지 않고도 처음부터 connection을 갖는다. `_resolve_custom_auth`의 legacy 분기가 진짜 "이행 잔여물"로만 유지됨.

### [S-3] Legacy 경로 로그 / 경고 (선택 — S3/S5에서 구현 여부 판단)

**현재**: `_resolve_legacy_tool_auth`는 조용히 동작. M3에서도 동일.

**제안**:
- `_resolve_custom_auth`의 legacy 분기 진입 시 `logger.debug`로 1회 tool_id 기록 (DEBUG 레벨이라 prod 소음 없음).
- M6 cleanup 전에 "실제로 얼마나 많은 CUSTOM tool이 legacy 경로를 타는지" 계측 가능 → drop 시점 판단 근거.

**효과**: 옵션. 젠슨 S3 진행 시 과부담이면 생략 가능. M5/M6에서 추가해도 늦지 않다.

---

## 3. 보류 (M6 cleanup 이월)

제거 대상이지만 M4 PR에서는 **건드리지 않는다**. 근거 + 제거 시점 기록만.

### [H-1] `chat_service._resolve_legacy_tool_auth`의 CUSTOM 경로

- **위치**: `backend/app/services/chat_service.py:302-315`
- **현 역할**: `tool.connection_id IS NULL AND tool.credential_id IS NOT NULL` CUSTOM tool의 credential 복호화.
- **제거 시점**: M6 — m11 backfill이 모든 CUSTOM row를 이관한 후 + 운영 계측으로 legacy 경로 trigger가 0임을 확인한 후.
- **제거 방법**: `_resolve_custom_auth`에서 legacy 분기 제거 + `_resolve_legacy_tool_auth`는 PREBUILT `provider_name IS NULL` 커버리지가 사라지면 파일 자체 삭제.

### [H-2] `tools.credential_id` 컬럼 + `Tool.credential` ORM 관계

- **위치**: `backend/app/models/tool.py` (line 조회 생략 — 수정 금지 파일)
- **현 역할**: CUSTOM + PREBUILT legacy bind. M3 기준 PREBUILT는 이미 connection으로 완전 이관. M4에서 CUSTOM도 이관.
- **제거 시점**: M6 — connection-only 운영 확정 후 drop + legacy_tool_auth helper 동시 제거.

### [H-3] `tools.auth_config` 컬럼 (inline secret)

- **위치**: `backend/app/models/tool.py`, `ToolCustomCreate.auth_config` (`schemas/tool.py:45`), `ToolAuthConfigUpdate.auth_config` (`schemas/tool.py:50`)
- **현 역할**: "credential 없이 tool 생성 직후 inline auth" 레거시. M3 이전 유저 시나리오.
- **제거 시점**: M6. 다만 ORM 컬럼 drop 전에 데이터 감사 필요 — prod에 `auth_config IS NOT NULL AND credential_id IS NULL` row가 있는지 확인.

### [H-4] `PATCH /api/tools/{tool_id}/auth-config` 엔드포인트의 `credential_id` 처리

- **위치**: `backend/app/routers/tools.py:115-128` + `backend/app/services/tool_service.py:249+` `update_tool_auth_config`
- **현 역할**: 3개 auth dialog(prebuilt/custom/MCP server)가 **공통으로** 쓰는 credential rebind 엔드포인트.
- **현 상태**: M3에서 PREBUILT dialog는 이미 `useConnections` POST/PATCH로 우회 — 이 엔드포인트의 PREBUILT 호출은 M3에서 이미 0에 가까움 (M3 저커버그 작업 참조).
- **M4 영향**: S4에서 `add-tool-dialog` Custom 탭을 find-or-create connection으로 바꾸면 **신규 생성 경로는 이 엔드포인트를 통과하지 않는다**. 기존 tool의 credential 재바인딩은 여전히 `custom-auth-dialog`(M5 이월) 경로로 이 엔드포인트를 호출.
- **제거 시점**: M5(custom-auth-dialog 교체) + M6(MCP dialog 교체) 완료 후 — 3 dialog 모두 connection 경유로 이관되면 이 엔드포인트 자체가 dead code. 엔드포인트 DELETE는 M6.

### [H-5] `ToolCustomCreate.credential_id` 필드 (`schemas/tool.py:46`)

- **현 역할**: POST /tools/custom 시 credential 직접 바인딩.
- **S4에서**: body에서 **보내지 않도록** 클라이언트 변경. 서버 스키마는 M6까지 유지 (하위호환).
- **제거 시점**: M6 — `Tool.credential_id` 컬럼 drop과 동시.

---

## 4. M5 이월 (현황만 기록)

**M4 스코프 밖**. 건드리지 않는다. 아래는 M5 진입 시 참조용 현황 스냅샷.

### [L-1] `frontend/src/components/tool/custom-auth-dialog.tsx` (109줄)

- **현 역할**: 기존 CUSTOM tool의 credential rebind. `useUpdateToolAuthConfig` → `PATCH /tools/{id}/auth-config`.
- **라인 16, 33**: `useUpdateToolAuthConfig` import + call.
- **라인 36**: `useState<string>(tool.credential_id ?? CREDENTIAL_NONE)` — `tool.credential_id`에 직접 의존.
- **라인 40-45**: save 시 `{ authConfig: {}, credentialId }` 전달 (inline auth 항상 초기화 + credential_id 갱신).
- **M5 교체 방향**: `ConnectionBindingDialog`(M3 신설) shell 재사용. credential → connection find-or-create → `tool.connection_id` PATCH 또는 `Connection.credential_id` PATCH.
- **주의**: M5까지 이 dialog는 **legacy 경로로 계속 동작**. M4의 백엔드 변경은 이 dialog를 **깨뜨리지 않아야** 한다 (젠슨 S3의 `_resolve_custom_auth`가 legacy 분기 tolerance 유지로 커버).

### [L-2] `frontend/src/components/tool/mcp-server-auth-dialog.tsx`

- **현 역할**: MCP server credential rebind. 동일한 `PATCH /tools/{id}/auth-config` 엔드포인트 경유 (추정 — 파일 미검증, M5에서 확인).
- **M4 영향**: 전혀 없음. MCP는 M2에서 이미 connection 경유로 런타임 동작. Dialog만 legacy API를 쓰는 상태.
- **M5 교체 방향**: `ConnectionBindingDialog` shell 재사용.

### [L-3] `/connections` 페이지 CUSTOM 섹션

- **현 상태**: `CredentialCard` 리스트 유지(M5까지 보존 — M3 zuckerberg 결정). PREBUILT 섹션만 M3에서 신규 추가.
- **M4 영향**: CUSTOM tool이 connection을 갖게 되지만 `/connections` 페이지의 CUSTOM 섹션은 여전히 credential 중심 뷰. UX 일관성 이슈는 M5에서 PrebuiltConnectionSection 패턴으로 `CustomConnectionSection` 추가로 해소.

---

## 5. 분석서 검증 체크리스트 (사티아 S6 게이트 참고)

- [x] 분석 대상 4개 파일 모두 읽음: `chat_service.py`, `routers/tools.py`, `add-tool-dialog.tsx`, `custom-auth-dialog.tsx`
- [x] 보조 확인: `services/tool_service.py` CUSTOM 경로, `schemas/tool.py` credential_id/auth_config 필드
- [x] 3분류(즉시/단순화/보류) + M5 이월 현황 구분
- [x] drive-by 금지 준수 — 실제 수정 0건, 분석서만 신규 생성
- [x] M6 precedent 기록 — H-1~H-5 각각 제거 시점 + 방법 명시
- [x] 스코프 합의 준수 — custom-auth-dialog, mcp-server-auth-dialog, `/connections` CUSTOM 섹션 미변경

---

## 6. 팀원에게 전달할 사전 사실 (S2~S5 진입 전 공유)

> 젠슨 S3 / 저커버그 S4 / 피차이 S2에 해당하는 정보만 아래 요약. 실제 파일 경계는 `progress.txt`의 표를 따른다.

1. **피차이 S2 (Alembic m11)** — `progress.txt` "Alembic m11 상세 설계" 그대로 충분. 분석서가 추가로 지시할 사항 없음. `(user_id, credential_id)` dedup + `M11_SEED_MARKER = "[m11-auto-seed]"` + `m11_custom_connection` 22자 revision ID 모두 합의대로.
2. **젠슨 S3 (`_resolve_custom_auth` 신설)** — [S-1] 권고: PREBUILT 대칭 구조. PREBUILT의 "connection 없음 = env fallback" vs CUSTOM의 "connection 없음 = legacy fallback" 시맨틱 차이 docstring 필수. `build_tools_config`의 `else:` 혼합 분기를 `elif tool.type == CUSTOM:` + `else: _resolve_legacy_tool_auth(tool)`(BUILTIN 전용)로 분리하면 M6 cleanup이 직관적.
3. **저커버그 S4 (add-tool-dialog Custom 탭)** — [S-2] 권고: Submit 시점 find-or-create connection → body에 `credential_id` 금지, `connection_id`만 전달. `ToolCreateRequest.connection_id?` 필드가 `frontend/src/lib/types/index.ts`에 이미 있는지 확인(M2에서 Tool에 추가됐지만 Create 요청 스키마에는 누락됐을 수 있음 — 누락 시 저커버그가 S4에서 추가). `useConnections` 훅은 M3 저커버그 산출.
4. **베조스 S5 (본인)** — 테스트 시나리오는 CHECKPOINT.md S5 체크리스트 그대로. 분석서 [S-3] 로그 계측은 선택 사항이므로 테스트에서도 생략 가능. `_resolve_custom_auth` 헬퍼가 실제로 만들어져 있어야 `inspect.getsource` 소스 계약 가드를 적용할 수 있으므로 S3 완료 후 진입. Alembic m11 왕복은 M9/M10 precedent대로 aiosqlite에서는 `inspect.getsource` 가드로, PG 실제 왕복은 S6 integration 게이트에서.
