# 삭제 분석 보고서 — 백로그 E M3 (PREBUILT per-user Connection)

**브랜치**: `feature/prebuilt-per-user-connection`
**작성자**: 베조스 (QA)
**작성일**: 2026-04-18
**ADR**: `docs/design-docs/adr-008-connection-entity.md`
**실행계획**: `docs/exec-plans/active/backlog-e-connection-refactor.md` (§4 M3)
**이전 보고서**: `tasks/deletion-analysis-e-m2.md`
**스코프 정의**: M3는 PREBUILT 도구가 per-user 기준으로 각자의 credential을 쓰도록 해석 경로를 바꾸는 마일스톤.
핵심 산출물 4종: (a) `tools.provider_name` 컬럼 + Alembic m10 + mock user env→credential→default connection 자동 시드,
(b) `chat_service.build_tools_config` PREBUILT 분기 재작성 + `connection_service.get_default_connection` 헬퍼,
(c) `ConnectionBindingDialog` 공통 셸 도입 + 기존 3 dialog 재배선 (M5 일부 당김),
(d) `/connections` PREBUILT 탭. `tools.credential_id` / `tools.auth_config` drop은 **M6 책임**. M3는 PREBUILT에서 credential_id를 "무시하도록 경로만 바꾸고" 컬럼은 그대로 유지하는 이행 단계.

---

## 요약 (즉시 삭제 1 / 단순화 6 / 보류 12)

M3는 PREBUILT hot path(`chat_service.build_tools_config` 비-MCP 분기)와 UI dialog 3종을 동시에 교체하는 **고위험 마일스톤**이다. legacy fallback(`provider_name IS NULL` + `credential_id` 경로)이 M6까지 보존되어야 하므로 백엔드 즉시 삭제는 0. 프론트엔드에서만 `ConnectionBindingDialog` 대체로 인해 제거 가능한 **유일한 유틸 함수 1개** + **3 dialog 파일 자체 제거**가 단순화 경로로 잡힌다.

---

## 분석 원칙

ADR-008 §이행 전략 + M3 `progress.txt` 파일 경계(피차이/젠슨/저커버그/베조스)를 기준으로, M3 PR이 닫힐 때 안전하게 제거/단순화 가능한 항목만 분류한다. M3 스코프 외 제거 제안은 모두 **보류**로 처리 — "지나가며 정리(drive-by)" 금지. `naver_tools.py`/`google_tools.py`의 `(auth_config or {}).get(...) or settings.*` 패턴은 ADR-008 §11 + 합의된 env fallback 유지 정책이므로 **절대 건드리지 않는다**.

---

## 사전 사실 확인

탐색 중 M3 담당자들이 알아야 할 기준선을 확인했다:

1. **`backend/app/models/tool.py`에는 현재 `provider_name` 컬럼이 없다.** M2에서 `connection_id`는 선반영되었으나 (`tool.py:70-72`), `provider_name`은 S2(피차이)에서 완전 신규 추가가 필요. M2처럼 부분 선반영이 없는 상황 — 피차이에게 명시적으로 전달.
2. **`Tool.connection_id` 컬럼은 이미 존재(M2 산출)**. 그러나 PREBUILT 경로에서는 **사용하지 않는다** — PREBUILT tool은 `user_id=NULL`인 공유 행이라 per-user 바인딩을 `tool.connection_id`에 저장할 수 없기 때문. ADR-008 §3대로 `user_id + tool.provider_name + is_default=true` 조회가 PREBUILT의 SOT. 이 사실이 S3(젠슨)의 쿼리 설계에서 혼동되지 않도록 사전 공유.
3. **`tool.credential`과 `tool.credential_id`는 M3 종료 후에도 CUSTOM 경로에서 계속 사용된다 (M4 대상)**. M3 PR에서 삭제 절대 금지. PREBUILT만 "무시"로 전환.
4. **`connection_service.get_default_connection` 헬퍼는 현재 없다.** S3(젠슨)가 신규 추가해야 하는 항목. 기존 `list_connections`에 `is_default=true` 필터를 추가하는 대신 신규 전용 헬퍼(sync/async 여부는 젠슨 판단 — progress.txt에 따르면 chat_service가 sync이므로 selectinload 또는 별도 sync select 중 선택)가 적절.
5. **`connection_service.py`는 M1 산출물이라 파일 경계상 M3에서 `get_default_connection` 추가 외 수정 금지**. 이는 M2 보고서 단순화 #3과 동일 원칙.
6. **env fallback은 tool builder 내부에 이미 구현**(`naver_tools.py`, `google_tools.py`의 `or settings.*` 패턴). 따라서 `chat_service`의 PREBUILT 분기에서 env fallback을 별도 처리할 필요 없음 — `cred_auth = {}`을 전달하면 tool builder가 자동으로 settings.* 사용. S3(젠슨)이 이 점을 혼동해 chat_service에 env 재조회 로직을 끼워넣으면 2중 fallback으로 코드 중복이 생긴다.

이 6건을 사티아가 S2/S3 담당자에게 전달하면 작업 누락/중복 작업을 방지한다.

---

## 즉시 삭제 가능

### 1. **`frontend/src/components/tool/prebuilt-auth-dialog.tsx:27-34` — `detectProvider` 헬퍼 함수**

현재:
```typescript
function detectProvider(toolName: string): string {
  const lower = toolName.toLowerCase()
  if (lower.startsWith('naver')) return 'naver'
  if (lower.startsWith('google chat')) return 'google_chat'
  if (lower.startsWith('gmail') || lower.startsWith('calendar')) return 'google_workspace'
  if (lower.startsWith('google')) return 'google_search'
  return 'unknown'
}
```

- **근거**: M3 S2에서 `tools.provider_name` 컬럼이 신설되고 백엔드 tool 응답 스키마에 `provider_name`이 포함되면, 프론트는 `tool.provider_name`을 직접 읽는다. tool **name 문자열 패턴 매칭**으로 provider를 추정하는 이 함수는 존재 이유가 사라진다. "Gmail" / "Calendar" / "Google Chat" / "Naver" 패턴이 네 곳에서 분기되는 매직 휴리스틱이 표본 오류(예: 새 PREBUILT 이름이 이 패턴과 어긋나는 경우 `unknown` 반환)의 근원.
- **영향 범위**: `prebuilt-auth-dialog.tsx` 내부에서만 호출됨. S4에서 `ConnectionBindingDialog` 도입 시 이 dialog 파일 자체가 `ConnectionBindingDialog` 기반 어댑터로 재작성되거나 제거되므로 함수도 자연스럽게 사라진다.
- **조건**: S2(피차이) 완료로 `tool.provider_name` 컬럼 + API 응답에 포함이 선행되어야 한다. S4(저커버그)가 이 조건 충족 후 제거.
- **drive-by 아닌 근거**: S4 스코프에 이미 `prebuilt-auth-dialog.tsx` 재배선이 포함되어 있음. 해당 작업 중 자연 삭제.

---

## 단순화 제안 (M3 PR 안에서 처리)

### 1. **`backend/app/services/chat_service.py:254-260` — PREBUILT/CUSTOM 분기 재작성** (젠슨 S3 가이드)

현재 구조(M2 종료 시점):
```python
else:  # PREBUILT 또는 CUSTOM (MCP가 아닌 모든 케이스)
    if tool.credential_id and tool.credential:
        cred_auth = resolve_credential_data(tool.credential)
    elif tool.auth_config:
        cred_auth = tool.auth_config
    else:
        cred_auth = {}
```

M3 후 필요 동작:
- `tool.type == PREBUILT AND tool.provider_name` → user default connection 조회 → `conn.credential` 있으면 `resolve_credential_data(conn.credential)`, 없으면 `cred_auth = {}` (env fallback은 tool builder 내부가 담당)
- `tool.type == PREBUILT AND tool.provider_name IS NULL` → legacy: 기존 `tool.credential_id`/`tool.auth_config` 경로 유지 (M6까지 tolerance)
- `tool.type == CUSTOM` → **기존 경로 그대로** (M4 작업 대상, 건드리지 말 것)

제안:
```python
else:
    if tool.type == ToolType.PREBUILT and tool.provider_name:
        cred_auth = _resolve_prebuilt_auth(tool, user_default_conn_map)
    else:
        # CUSTOM 또는 legacy PREBUILT(provider_name IS NULL)
        cred_auth = _resolve_legacy_tool_auth(tool)
```

- `_resolve_prebuilt_auth(tool, user_default_conn_map)`: 모듈-private. `(tool.user_id, tool.provider_name)` 키로 `user_default_conn_map`에서 connection lookup → `resolve_credential_data(conn.credential)` 또는 `{}` 반환. **cross-tenant 가드**(`assert_credential_ownership`)는 M2 패턴 재사용.
- `_resolve_legacy_tool_auth(tool)`: 현재 254-260의 4-way fallback 그대로 추출(동작 보존).
- **근거**: 4-way fallback이 2중으로 중첩되면 가독성/회귀 위험 증가. 헬퍼 2개로 분리 시 M4에서 CUSTOM을 connection 경유로 바꿀 때 `_resolve_legacy_tool_auth` 한 곳만 수정하면 됨. M6에서 legacy drop 시도 1곳.
- **새 추상화 금지**: 별도 클래스/모듈 만들지 말 것. `chat_service` 모듈 내부의 모듈-private 함수 2개만.
- **drive-by 방지**: CUSTOM 분기 로직(`tool.credential_id`/`tool.auth_config` fallback)은 **시맨틱 변경 0**. 단순히 PREBUILT를 분리해 내는 리팩토링.

### 2. **`backend/app/services/chat_service.py:152-174` — `get_agent_with_tools` selectinload 확장** (젠슨 S3 가이드)

현재: `selectinload(Tool.connection).selectinload(Connection.credential)` 체인이 MCP용으로 존재. PREBUILT default connection은 **tool별이 아니라 (user, provider) 스코프**라 selectinload 체인으로 연결 불가.

제안:
- `get_agent_with_tools` 끝부분에 agent.tool_links 순회하며 **PREBUILT + provider_name 보유 tool 집합**에서 `(user_id, provider_name)` 목록을 수집.
- user_default_conn_map: 별도 쿼리 1회 — `SELECT * FROM connections WHERE user_id=:u AND type='prebuilt' AND provider_name IN (:providers) AND is_default=true` + `selectinload(Connection.credential)`.
- dict로 매핑해 `build_tools_config`에 주입. N+1 방지의 핵심.
- **근거**: progress.txt 고위험 포인트 #1 직접 대응. PREBUILT tool N개마다 connection 쿼리를 날리면 N+1. IN 쿼리 1회로 O(1) round-trip.
- **단순성 원칙**: 캐시 레이어 도입 금지. `dict[(user_id, provider_name), Connection]` 하나만 in-memory로 전달. 쿼리 결과가 요청 수명 동안만 살아있음.

### 3. **`backend/app/services/chat_service.py:273-278` — MCP 전용 config 키 그대로 유지** (젠슨 S3 가이드)

```python
if tool.type == ToolType.MCP and mcp_server_url is not None:
    config_entry["mcp_server_url"] = mcp_server_url
    config_entry["mcp_tool_name"] = tool.name
    if mcp_transport_headers:
        config_entry["mcp_transport_headers"] = mcp_transport_headers
```

- **제안**: **변경 금지**. PREBUILT가 새 경로를 쓴다고 이 MCP 전용 블록을 건드리지 말 것. M2에서 이미 안정화된 경로.
- **근거**: MCP 분기는 M2 PR #54에서 Codex 6차 adversarial 검증을 통과한 코드. PREBUILT 수정 PR에서 시맨틱을 건드리면 회귀 표면적이 곱연산으로 늘어남.
- **조치**: S3에서 새 PREBUILT 헬퍼만 추가, 이 MCP 블록은 1줄도 수정하지 않음을 명시.

### 4. **`frontend/src/components/connection/ConnectionBindingDialog.tsx` 신규 공통 셸 + 3 dialog 재배선** (저커버그 S4 가이드)

3 dialog 파일(`prebuilt-auth-dialog.tsx` / `custom-auth-dialog.tsx` / `mcp-server-auth-dialog.tsx`)은 구조상 **CredentialSelect + 저장 버튼 + CredentialFormDialog 조합**이 90% 동일. 저장 시 호출하는 API만 다름:

| Dialog | 저장 API |
|--------|---------|
| prebuilt-auth-dialog | `useUpdateToolAuthConfig({authConfig: {}, credentialId})` |
| custom-auth-dialog | `useUpdateToolAuthConfig({authConfig: {}, credentialId})` (동일) |
| mcp-server-auth-dialog | `useUpdateMCPServer({credential_id})` |

현재 차이점이라고 부를 만한 것:
- `prebuilt-auth-dialog`의 `detectProvider` + `matchingCredentials` 필터 (즉시 삭제 #1)
- `mcp-server-auth-dialog`의 `open/onOpenChange`를 외부에서 제어 (나머지 둘은 내부 상태)
- i18n 네임스페이스(`tool.authDialog` / `tool.customAuth` / `tool.mcpServer.auth`)

제안:
- `ConnectionBindingDialog` props: `{ type: 'prebuilt' | 'custom' | 'mcp', toolId?: string, mcpServerId?: string, providerName?: string, open?, onOpenChange?, trigger? }`
- 내부: `CredentialSelect` + `CredentialFormDialog` 재사용. `type` 분기는 저장 시 API 분기에만 국한.
- 3 파일은 **얇은 어댑터로 축소하거나 직접 호출부에서 `ConnectionBindingDialog`로 대체**. S4에서 저커버그 판단 — 둘 중 덜 표면적을 키우는 쪽.
- **근거**: ADR-008 §긍정 "UI 통합 — 3개 auth 다이얼로그 → ConnectionBindingDialog 1개 + context prop (백로그 F 흡수)". M3 스코프 합의에서 M5 일부(F) 당김에 포함됨.
- **신규 추상화 경계**: `ConnectionBindingDialog`는 단일 파일 컴포넌트. HOC/커스텀 훅 추가 금지. `type` prop 1개로 모든 분기 수용.

### 5. **`frontend/src/app/connections/page.tsx` — PREBUILT 탭 추가, 기존 Credential 리스트 유지** (저커버그 S4 가이드)

현재 page.tsx는 **credentials 리스트**만 렌더(`CredentialCard`). ADR-008 §긍정 / CHECKPOINT S4 "/connections 페이지 재편"에 따라 M3에서 PREBUILT connection을 노출해야 함.

제안:
- 기존 `CredentialCard` 리스트는 **유지** (M5에서 Connection 중심으로 재편 예정).
- 상단 또는 별도 탭(예: Tabs)에 "연결(Connections)" 섹션 추가 — PREBUILT provider 드롭다운 + default 토글 + credential 선택.
- **단순화 포인트**: 기존 page.tsx를 재작성하지 말고 section 추가. M5까지 Credential 중심 UX 병존.
- **근거**: CHECKPOINT §리스크 #4 "Frontend ConnectionBindingDialog 통합 난이도". 같은 PR에서 page.tsx 전체 재편까지 시도하면 회귀 표면이 폭증. M3는 "PREBUILT connection 편집이 가능한 UX 제공"까지가 목표.
- **drive-by 방지**: `CredentialCard` 컴포넌트(190-256), `credentials` 필터링 로직(61-75), delete 플로우(158-185)는 그대로. 시맨틱 변경 금지.

### 6. **테스트 격리 정책 — 신규 1개 파일, 기존 PREBUILT 테스트는 mechanical change만** (베조스 본인 S5 가이드)

- `tests/test_connection_prebuilt_resolve.py` 신규 1개에 **5+ 시나리오** 격납 (M2 패턴 반복):
  1. mock user 2명이 같은 PREBUILT tool을 각자 credential로 실행 → 각자 `resolve_credential_data` 결과가 다름을 검증 (ADR-008 §M3 테스트 #1 "다중 유저 격리 필수")
  2. default connection 없을 때 `cred_auth={}` 반환 + tool builder의 env fallback 유지 검증
  3. `provider_name IS NULL`인 legacy PREBUILT tool → `tool.credential_id` 경로 여전히 동작 (M6까지 tolerance)
  4. Alembic m10 mock user 자동 시드 idempotent (같은 `(user_id, type='prebuilt', provider_name, is_default=true)` 조합 재실행 시 `IntegrityError` 없이 skip)
  5. Cross-tenant 가드: user_A의 connection이 user_B가 실행한 PREBUILT tool에 잘못 매핑되어도 `assert_credential_ownership`이 차단 (M2에서 도입한 가드 재사용 증명)
- `tests/test_naver_tool.py`, `tests/test_tools_router_extended.py` 등 기존 PREBUILT 테스트는 **시맨틱 변경 0을 목표**. `provider_name` 컬럼 추가로 fixture에 필드 하나 늘어나는 mechanical change만 허용. 기대값(assert) 변경이 필요하면 사티아에게 사유 보고.
- **근거**: M2 보고서 단순화 #5와 동일 원칙. legacy fallback + env fallback이 기존 테스트의 시맨틱을 보존해야 함. 변경되면 그 자체가 regression 시그널.

---

## 보류 (후속 마일스톤으로 이월)

M3 PR에서 **절대 건드리지 않음**. 이전 M1/M2 보고서 이월분 + M3 분석에서 추가 식별.

| # | 위치 | 현재 역할 | 권고 시점 | 사유 |
|---|------|-----------|-----------|------|
| 1 | `backend/app/models/tool.py:80-82` `Tool.credential_id` FK | PREBUILT(legacy)/CUSTOM 공용 credential 바인딩 | **M6** (`m12_drop_legacy_columns`) | M3는 PREBUILT에서 무시. CUSTOM은 M4까지 사용. `is_system=True + provider_name IS NULL` 케이스의 legacy fallback에도 필요 → M6까지 drop 불가. |
| 2 | `backend/app/models/tool.py:79` `Tool.auth_config` JSON | PREBUILT(legacy)/CUSTOM inline auth | **M6** | 동일. CUSTOM 경로 + legacy PREBUILT fallback. |
| 3 | `backend/app/models/tool.py:78` `Tool.auth_type` VARCHAR | CUSTOM HTTP auth 타입 (`basic`/`bearer`/...) | **M6** | CUSTOM 경로가 여전히 참조. chat_service에 `config_entry["auth_type"] = tool.auth_type`로 노출. |
| 4 | `backend/app/models/tool.py:30` `AgentToolLink.config` JSON | per-agent 도구 override | **M6** | ADR-008 §3 — `agent_tools.connection_id`(M3+ 지원 가능하나 이행 계획상 M5)로 대체, M6 drop. |
| 5 | `backend/app/services/credential_service.py:110-119` `resolve_server_auth` | MCPServer auth 해석 헬퍼 | **M6** | M2 보고서 #5 이월. legacy MCP fallback 경로에서 호출. |
| 6 | `backend/app/services/credential_service.py:122-142` `get_usage_count` | credential 사용량 집계 | **M5/M6** | M5에서 `connection_count` 추가 후 M6 drop. M3에서는 Tool/MCPServer 집계 유지. 베조스/젠슨 모두 수정 금지 영역. |
| 7 | `backend/app/models/tool.py:35-58` `MCPServer` 테이블 전체 | MCP 서버 메타데이터 | **M6** | M2 보고서 #1 이월. |
| 8 | `backend/app/models/tool.py:69` `Tool.mcp_server_id` FK | MCP tool → MCPServer 링크 | **M6** | M2 보고서 #2 이월. |
| 9 | `backend/app/seed/default_tools.py` 1-357 현재 구조 | PREBUILT tool seed 메타 (provider_name 없음) | **S2 내 갱신, 제거 아님** | S2(피차이)에서 각 PREBUILT entry에 `provider_name` 키 추가 필요. 기존 field 제거 대상 없음. Web Search / Web Scraper / Current DateTime 같은 `type='builtin'` entry는 `provider_name` NULL 유지. |
| 10 | `frontend/src/components/tool/credential-select.tsx` | Credential 선택 드롭다운 | **M5** | `ConnectionBindingDialog` 내부에서 계속 재사용. M5에서 Connection 중심 UI로 재편 시 CredentialSelect가 ConnectionSelect 보조 컴포넌트로 재위치 가능. |
| 11 | `frontend/src/app/connections/page.tsx:190-256` `CredentialCard` | credential 리스트 카드 | **M5** | 단순화 #5에 따라 M3에서는 유지. M5에서 ConnectionCard로 교체. |
| 12 | `frontend/src/lib/hooks/use-tools.ts` `useUpdateToolAuthConfig` | tool auth_config/credential_id PATCH 훅 | **M4-M5** | CUSTOM 경로에서 계속 쓰임. M4 완료 후 connection 기반 훅으로 대체. M3에서 수정 금지. |

---

## 보안/회귀 점검 (M3 PR에서 반드시 검증)

M3 hot path 변경이 유발할 수 있는 회귀. 베조스 S5 신규 테스트에 반영:

1. **PREBUILT cross-tenant credential 유출 방지 (ADR-008 §문제 1의 본질)**
   - user_A의 default connection이 user_B가 실행한 같은 PREBUILT tool에 절대 매핑되어서는 안 됨.
   - 검증: `test_connection_prebuilt_resolve.py`에 "2 user + 같은 tool + 각자 credential" 시나리오 + `assert_credential_ownership` 호출 경로 검증.

2. **env fallback 유지 (ADR-008 §11 합의)**
   - connection이 없는 PREBUILT tool 실행 시 `naver_tools.py` 내부에서 `settings.naver_client_id`를 자동으로 사용해야 함.
   - 검증: connection 0개 상태 + `NAVER_CLIENT_ID` env 설정된 상태에서 Naver Blog Search 실행 시 응답이 env 값으로 나옴 (mock HTTP call로 헤더/파라미터 검증).

3. **legacy fallback 유지 (provider_name IS NULL)**
   - m10 이후에도 매핑 실패한 PREBUILT tool(예: 새로 추가된 tool 이름이 매핑 표와 어긋남)은 `provider_name=NULL`로 남음. 이 경우 기존 `tool.credential_id` 경로가 동작해야 함 — 기존 PoC 사용자가 configure한 credential이 끊기지 않도록.
   - 검증: 신규 테스트에 provider_name=NULL 시나리오 1건 추가.

4. **m10 idempotent**
   - 같은 `(user_id, type='prebuilt', provider_name, is_default=true)` 조합은 partial unique index로 막혀 있음. m10이 두 번째 upgrade/다른 환경에서 실행될 때 `INSERT`가 `IntegrityError` 없이 skip되도록 "존재 여부 체크 + insert only" 패턴 필수.
   - 검증: `test_connection_prebuilt_resolve.py`에 idempotent 시나리오.

5. **env var 미설정 환경에서 m10 skip**
   - `settings.naver_client_id`가 빈 문자열/None이면 해당 provider의 credential+connection 시드를 만들지 말아야 함 (ENCRYPTION_KEY 미설정과 동일 정책, ADR-007 패턴).
   - 검증: env를 일시 unset한 상태의 alembic upgrade에서 connection 0건 확인.

6. **응답 스키마에 credential 평문 미노출**
   - `tool.provider_name` 노출은 OK(비민감). 단 connection 응답에서 `credential.data_decrypted`/`data_encrypted`가 절대 echo되지 않도록 기존 M1/M2 스키마 가드 재검증.
   - 검증: M1의 `test_connections.py` 응답 스키마 assertion이 그대로 PASS해야 함 (기존 회귀 유지).

---

## drive-by 금지 선언

**M3 스코프**: `tools.provider_name` 컬럼 + Alembic m10(컬럼 백필 + mock user 시드) + `chat_service.build_tools_config` PREBUILT 분기 재작성 + `connection_service.get_default_connection` 헬퍼 추가 + `ConnectionBindingDialog` 신규 + 3 dialog 재배선 + `/connections` PREBUILT 탭. 위 단순화 6건은 모두 이 스코프 안에서 처리.

**스코프 외 legacy 제거는 M4(CUSTOM Connection 이관) / M5(UI 재편, agent_tools.connection_id override) / M6(legacy 컬럼 drop)에서 처리**한다. 본 PR에서:

- `tools.credential_id`, `tools.auth_config`, `tools.auth_type` 컬럼을 drop하지 않는다
- `mcp_servers` 테이블과 `tools.mcp_server_id`를 건드리지 않는다
- `agent_tools.config` JSON을 건드리지 않는다
- `credential_service.py`의 시그니처를 변경하지 않는다 (`get_default_connection`은 `connection_service.py`에만 추가)
- `naver_tools.py` / `google_tools.py` / `google_workspace_tools.py`의 env fallback 패턴을 건드리지 않는다
- CUSTOM 분기(`_resolve_legacy_tool_auth`)의 시맨틱을 변경하지 않는다 (단순 리팩토링만)
- `/connections` 페이지의 기존 `CredentialCard` 리스트를 재작성하지 않는다 (PREBUILT 탭 추가만)

---

## 결론

| 분류 | 건수 | 비고 |
|------|------|------|
| **즉시 삭제** | **1건** | `prebuilt-auth-dialog.tsx`의 `detectProvider` 헬퍼 — S4 내 자연 삭제. |
| **단순화 제안** | **6건** | S3(젠슨) 3건 + S4(저커버그) 2건 + S5(베조스) 1건. 기존 모듈 시그니처 변경 0. |
| **보류 (후속 마일스톤)** | **12건** | M4 1건, M5 3건, M5/M6 1건, M6 7건. M1/M2 보고서 이월 + M3 특화 3건 추가. |

**베조스 판단**: M3는 (a) 새 컬럼 + 데이터 마이그레이션 + mock user 자동 시드, (b) hot path(`chat_service.build_tools_config` 비-MCP 분기) 재작성 + 신규 N+1 방지 쿼리, (c) UI dialog 3종 통합(M5 일부 당김)이라는 **세 개의 고위험 축**이 동시 이동하는 마일스톤이다. M2의 MCP hot path 재작성보다 표면적이 넓다.

legacy fallback(`provider_name IS NULL` + `tool.credential_id`)이 M6까지 보존되는 것이 ADR-008 §이행 전략의 핵심 약속이며, 이번 PR에서 legacy 컬럼을 drop하거나 CUSTOM 분기를 건드리면 회귀 영역이 곱연산으로 폭발한다. 단순화 6건은 모두 **기존 시그니처를 보존하고 신규 헬퍼/컴포넌트 분리 + 얇은 어댑터**로 해결하는 방향으로, 이미 넓은 PR 표면에 추가 위험을 보태지 않는다.

env fallback 정책(ADR-008 §11)에 따라 `naver_tools.py`/`google_tools.py` 내부의 `or settings.*` 패턴은 **M3 종료 후에도 유지**된다. `chat_service`가 connection 없을 때 `cred_auth={}`을 전달하면 tool builder가 자동으로 env를 사용하는 2단 구조가 이미 동작한다 — S3(젠슨)이 chat_service에 env 재조회 로직을 끼워넣지 않도록 사전 사실 #6에 명시했다.

**사티아 보고**: 사전 사실 6건(특히 #1 provider_name 미선반영 / #2 PREBUILT가 `tool.connection_id` 미사용 / #4 `get_default_connection` 미존재 / #6 env fallback 2중 처리 방지)은 S2/S3 담당자에게 **명시적으로 전달 권고**. 단순화 #1(chat_service 헬퍼 2개 분리) + #2(user_default_conn_map 주입)는 S3 구현의 뼈대이므로 젠슨에게 1:1 공유 필요.
