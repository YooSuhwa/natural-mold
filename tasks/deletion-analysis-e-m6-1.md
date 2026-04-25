# M6.1 — 삭제 분석 보고서 (deletion-analysis-e-m6-1)

**작성자**: 베조스 (QA/삭제 분석 DRI)
**작성일**: 2026-04-24
**브랜치**: `feature/backlog-e-m6-1` @ `18d98be` base
**산출물 위치**: `tasks/deletion-analysis-e-m6-1.md`
**계획서**: `/Users/chester/.claude/plans/m6-1-spicy-kurzweil.md`
**최종 판정**: **🟢 GREEN** — M2/M4 즉시 착수 가능

---

## 요약 (숫자로)

| 지표 | 값 |
|------|----|
| Backend 제거(D) 대상 파일 | **8개** |
| Backend 재작성(R) 대상 파일 | **1개** (`routers/tools.py` `test_mcp_connection`) |
| Backend 신규(N) 파일 | **1개** (`alembic/versions/m13_drop_mcp_legacy.py`) |
| Frontend 제거(D) 대상 파일 | **2개** (`mcp-server-rename-dialog.tsx` 전체 삭제 + mcp 참조 cleanup) |
| Frontend 신규(N) 파일 | **1개** (`binding-dialog-shell.tsx`) |
| `MCPServer`/`mcp_server_id`/`resolve_server_auth` 백엔드 grep 히트 | **57건 / 9 파일** |
| 동일 심볼 프론트 grep 히트 | **53건 / 10 파일** |
| 제거 대상 백엔드 테스트 파일 | **4개** (`test_connection_mcp_resolve.py` / `test_tools.py` 일부 / `test_tools_router_extended.py` 일부 / `test_connections.py` 참조) |
| 영향받는 alembic head | `m12_drop_legacy_columns` → `m13_drop_mcp_legacy` (신규) |

Grep 실측:
- `rg "mcp_server_id|MCPServer|resolve_server_auth" backend/app/` → **57 hits / 9 files**
- `rg "mcp-server" backend/app/routers/` → **6 hits / 1 file** (`routers/tools.py`)
- `rg "updateMCPServer|useUpdateMCPServer|useRegisterMCPServer|useMCPServers|useDeleteMCPServer|mcp_server_id|MCPServer" frontend/src/` → **53 hits / 10 files**

---

## 1. Backend 제거/재작성 매트릭스

태그: **D** = Delete, **R** = Rewrite (로직 보존, 의존성 교체), **K** = Keep (M6.1 미포함)

### 1.1 `backend/app/models/tool.py`

| 라인 | 심볼 | 태그 |
|------|------|------|
| L34-57 | `class MCPServer(Base)` 전체 | **D** |
| L72 | `mcp_server_id: Mapped[uuid.UUID \| None] = mapped_column(ForeignKey("mcp_servers.id"))` | **D** |
| L88 | `mcp_server: Mapped[MCPServer \| None] = relationship(back_populates="tools")` | **D** |
| L13 | `from app.models.credential import Credential` (TYPE_CHECKING) | **K** (다른 곳이 쓸 수 있음 — 현재 MCPServer만 쓰므로 drop 가능하지만 현 상태 유지해도 해가 없음) |

### 1.2 `backend/app/models/__init__.py`

| 라인 | 심볼 | 태그 |
|------|------|------|
| L12 | `from app.models.tool import AgentToolLink, MCPServer, Tool` → `AgentToolLink, Tool` | **D** (import 축소) |
| L23 | `"MCPServer",` export | **D** |

### 1.3 `backend/app/schemas/tool.py`

| 라인 | 심볼 | 태그 |
|------|------|------|
| L34-39 | `class MCPServerCreate` | **D** |
| L47 | `mcp_server_id: uuid.UUID \| None` 필드 (ToolResponse) | **D** |
| L62-72 | `class MCPServerResponse` | **D** |
| L83-94 | `class MCPServerListItem` | **D** |
| L97-100 | `class MCPServerUpdate` | **D** |
| L11-17 | `ToolType` enum 자체 — `MCP = "mcp"`는 **K** (connection_id path에서 계속 사용) |
| 신규 | `class ToolUpdate(BaseModel)` + `model_config = ConfigDict(extra="forbid")` + `connection_id: uuid.UUID \| None = None` | **N** (M2) |

### 1.4 `backend/app/services/tool_service.py`

| 라인 | 심볼 | 태그 |
|------|------|------|
| L10 | `from app.models.tool import AgentToolLink, MCPServer, Tool` → `MCPServer` 제거 | **D** |
| L11 | `from app.schemas.tool import MCPServerCreate, ToolCustomCreate, ToolType` → `MCPServerCreate` 제거 | **D** |
| L80-120 | `register_mcp_server()` | **D** |
| L123-130 | `get_mcp_servers()` | **D** |
| L133-179 | `list_mcp_server_items()` | **D** |
| L182-194 | `_apply_credential_update()` (private helper for MCPServer) | **D** |
| L197-228 | `update_mcp_server()` | **D** |
| L231-250 | `delete_mcp_server()` | **D** |
| 신규 | `async def update_tool(db, tool_id, user_id, payload: ToolUpdate) -> Tool` | **N** (M2) |

→ **제거 함수 6종** (L80-250 블록, ~170줄). 신규 1종.

### 1.5 `backend/app/services/credential_service.py`

| 라인 | 심볼 | 태그 |
|------|------|------|
| L12 | `from app.models.tool import AgentToolLink, MCPServer, Tool` → `MCPServer` 제거 | **D** |
| L112-121 | `def resolve_server_auth(server: MCPServer)` | **D** |
| L164-170 | `mcp_count_result` 블록 + `mcp_server_count` 반환 필드 (get_usage_count) | **D** — 반환 dict에서 `mcp_server_count` 키 제거, 호출처(frontend) 연동 확인 필요 |

**Scope note**: `get_usage_count`의 반환 shape 변경은 `schemas/credential.py::CredentialUsage` + frontend `CredentialUsage` 타입 필드 제거까지 연결됨 (아래 §3.1 참조).

### 1.6 `backend/app/services/chat_service.py`

| 라인 | 심볼 | 태그 |
|------|------|------|
| L18 | `from app.models.tool import AgentToolLink, MCPServer, Tool` → `MCPServer` 제거 | **D** |
| L26 | `resolve_server_auth,` import | **D** |
| L194-195 | `.selectinload(Tool.mcp_server).selectinload(MCPServer.credential),` prefetch | **D** |
| L384-392 | `elif tool.mcp_server is not None:` fallback 분기 (주석에 "M6.1에서 제거") | **D** |
| L393-394 | `else: cred_auth = {}` branch는 MCP에서만 쓰이던 third state — connection_id 없으면 fail-closed로 전환 권장 (R5 참조) | **R** |

**fallback 제거 후 기대 동작** (chat_service L343-402):
```python
if tool.type == ToolType.MCP:
    if tool.connection_id is not None and tool.connection is not None:
        # ... (기존 M2+ 경로 그대로) ...
    else:
        # M6.1: fail-closed (MCP legacy 경로 없음)
        raise ToolConfigError(
            f"MCP tool '{tool.name}' has no connection — execution blocked."
        )
```
CUSTOM path와 정합. 기존 `else: cred_auth = {}`는 L394에서 제거하고 `ToolConfigError`로 교체.

### 1.7 `backend/app/schemas/connection.py`

| 라인 | 심볼 | 태그 |
|------|------|------|
| L125 | `resolve_server_auth`가 credential 전체를 auth로 반환하던` 주석 | **K** (docstring 문맥 유지, 원하면 용어 정리만) |
| L186 | `MCPServerResponse`의 `auth_config` 전체 redaction 정책과 정합` 주석 | **D** (주석에서 `MCPServerResponse` 참조 제거) |

### 1.8 `backend/app/routers/tools.py`

| 라인 | 심볼 | 태그 |
|------|------|------|
| L11-14 | `MCPServerCreate, MCPServerListItem, MCPServerResponse, MCPServerUpdate` import | **D** |
| L48-54 | `POST /mcp-server` (register) | **D** |
| L57-62 | `GET /mcp-servers` (list) | **D** |
| L65-76 | `PATCH /mcp-servers/{server_id}` | **D** |
| L79-87 | `DELETE /mcp-servers/{server_id}` | **D** |
| L90-111 | `POST /mcp-server/{server_id}/test` (test_mcp_connection) | **R** (§2 재작성 명세 참조) |
| L9 | `from app.error_codes import mcp_server_not_found, tool_not_found` | **D** `mcp_server_not_found` 제거 |
| 신규 | `PATCH /api/tools/{tool_id}` (ToolUpdate → tool_service.update_tool) | **N** (M2) |

→ **라우트 4개 완전 제거 + 1개 재작성 + 1개 신설**. router 순서 정리 시 `DELETE /{tool_id}` (L114)가 마지막에 위치하도록 유지 (FastAPI path 우선순위).

### 1.9 `backend/app/error_codes.py`

| 심볼 | 태그 |
|------|------|
| `mcp_server_not_found()` factory 함수 | **D** (M3에서 완전 제거) |

### 1.10 `backend/app/agent_runtime/tool_factory.py` / `executor.py`

| 심볼 | 태그 |
|------|------|
| `executor.py` L263-267, L401 (`mcp_server_url`, `mcp_tool_name`, `mcp_transport_headers` 키) | **K** (이 키들은 `chat_service.build_tools_config`가 채우는 것이며 **connection 경로**에서 그대로 사용됨. 제거 대상 아님) |

→ runtime은 이미 `connection.extra_config` 경유. 수정 불필요.

### 1.11 `backend/app/main.py` / `services/legacy_invariants.py`

| 심볼 | 태그 |
|------|------|
| `_enforce_m6_legacy_invariants` startup guard | **N** 확장 (m13 preflight 추가) |
| `legacy_invariants.py` | **N** (m13 helper 추가: `assert_no_dangling_mcp_server_refs`) |

---

## 2. `test_mcp_connection` 재작성 명세 (R 태그)

### 2.1 현재 (routers/tools.py L90-111)

```python
@router.post("/mcp-server/{server_id}/test")
async def test_mcp_connection(server_id: uuid.UUID, db, user):
    result = await db.execute(
        select(MCPServer).where(MCPServer.id == server_id, MCPServer.user_id == user.id)
    )
    server = result.scalar_one_or_none()
    if not server: raise mcp_server_not_found()
    effective_auth = resolve_server_auth(server)      # credential 우선 → auth_config fallback
    test_result = await mcp_test(server.url, effective_auth)
    return test_result
```

### 2.2 제안 (신규 라우트 — connection 경유)

**라우트 경로**: `POST /api/connections/{connection_id}/test` (connection router로 이동 — router 스코프 재정렬)

또는 scope 보존을 위해 `POST /api/tools/{tool_id}/test` (tool → connection 체인)로 유지. **후자를 권장** — URL 변경 최소화 + `tool.type='mcp'`인 경우에만 허용.

의사코드:
```python
from app.agent_runtime.mcp_client import test_mcp_connection as mcp_test
from app.models.connection import Connection
from app.services.credential_service import resolve_credential_data
from app.services.env_var_resolver import resolve_env_vars

@router.post("/{tool_id}/test")
async def test_tool_connection(tool_id: uuid.UUID, db, user):
    tool = await db.get(Tool, tool_id, options=[
        selectinload(Tool.connection).selectinload(Connection.credential)
    ])
    if not tool or tool.user_id != user.id:
        raise tool_not_found()
    if tool.type != ToolType.MCP or tool.connection is None:
        raise HTTPException(400, "Only MCP tools with a bound connection can be tested")

    conn = tool.connection
    extra = conn.extra_config or {}
    url = extra.get("url")
    if not url:
        raise HTTPException(422, "connection.extra_config.url is missing")

    effective_auth = resolve_env_vars(
        extra.get("env_vars"), conn.credential,
        context={"connection_id": str(conn.id), "tool_name": tool.name},
    )
    return await mcp_test(url, effective_auth)
```

**포인트**:
- `resolve_server_auth` 대체 = `resolve_env_vars(extra.env_vars, conn.credential, context=…)` (기존 `chat_service.build_tools_config` L369-376 path와 동일)
- transport headers는 runtime에서만 필요하고 test에는 불필요 → 생략
- 인증/IDOR: `tool.user_id == user.id` 체크. connection ownership은 tool 생성 단계에서 이미 보장됨 (L57-58 validate_connection_for_custom_tool — MCP도 M5.5 이전까지는 tool 생성 시 connection을 거치므로 보수적으로 `conn.user_id == user.id` 재확인 권장)

### 2.3 프론트 API 클라이언트 영향

`frontend/src/lib/api/tools.ts::testMCPConnection(serverId)` → `testMCPConnection(toolId)`로 파라미터 의미 변경.
- 호출처(`rg "testMCPConnection" frontend/src/`)가 0건 (미사용 함수)이면 단순 삭제.
- **M5 저커버그 스코프**에 "testMCPConnection 호출처 전수 확인 후 정리 또는 tool-id 기반으로 재작성" 포함시킬 것.

---

## 3. Frontend 제거/재작성 매트릭스

### 3.1 `frontend/src/lib/types/index.ts`

| 라인 | 심볼 | 태그 |
|------|------|------|
| L210-213 | `CredentialUsage { tool_count, mcp_server_count }` → `mcp_server_count` 필드 | **D** |
| L220 | `Tool.mcp_server_id: string \| null` | **D** |
| L316-324 | `interface MCPServer` | **D** |
| L332-342 | `interface MCPServerListItem` | **D** |
| L344-348 | `interface MCPServerUpdateRequest` | **D** |
| L360-366 | `interface MCPServerCreateRequest` | **D** |
| 신규 | `interface ToolUpdateRequest { connection_id?: string \| null }` | **N** (M4) |

### 3.2 `frontend/src/lib/api/tools.ts`

| 라인 | 심볼 | 태그 |
|------|------|------|
| L4-8 | `MCPServer, MCPServerListItem, MCPServerUpdateRequest, MCPServerCreateRequest` import | **D** |
| L15-19 | `registerMCPServer` | **D** (M5) |
| L20-24 | `testMCPConnection` | **R** (§2.3) |
| L26 | `listMCPServers` | **D** (M5) |
| L27-31 | `updateMCPServer` | **D** (M5) |
| L32-33 | `deleteMCPServer` | **D** (M5) |
| 신규 | `update: (id, { connection_id }) => apiFetch<Tool>(`/api/tools/${id}`, { method: 'PATCH', body: JSON.stringify({ connection_id }) })` | **N** (M4) |

### 3.3 `frontend/src/lib/hooks/use-tools.ts`

| 라인 | 심볼 | 태그 |
|------|------|------|
| L13-14 | `MCPServerCreateRequest, MCPServerUpdateRequest` import | **D** |
| L22-25 | `invalidateMCPAndTools` helper | **D** (M5) |
| L39-45 | `useRegisterMCPServer` | **D** (M5) |
| L55-61 | `useMCPServers` | **D** (M5) |
| L71-95 | `useToolsByConnection` — MCP 브랜치 (L76-84) | **R** (M5): `mcp_servers` 기반 이중 hop 제거 후 `tool.connection_id` 단일 매칭으로 축소. **PREBUILT/CUSTOM 로직은 그대로 유지.** |
| L97-106 | `useUpdateMCPServer` | **D** (M5) |
| L108-114 | `useDeleteMCPServer` | **D** (M5) |
| 신규 | `export function useUpdateTool()` — `mutationFn: ({ id, data }) => toolsApi.update(id, data)`, `onSuccess: invalidate(['tools']) + invalidate(['agents'])` | **N** (M4) |

**`useUpdateTool` 네이밍 충돌 재확인**: 현 파일에 `useUpdateTool`은 존재하지 않음 (grep로 확인). `useCreateCustomTool`, `useDeleteTool`, `useRegisterMCPServer`, `useUpdateMCPServer` 등만 존재. 충돌 없음 → 해당 이름 사용 가능.

### 3.4 `frontend/src/components/connection/connection-binding-dialog.tsx`

공통 — §4 BindingDialogShell 추출 참조.

| 라인 | 심볼 | 태그 |
|------|------|------|
| L36 | `import { useUpdateMCPServer } from '@/lib/hooks/use-tools'` | **D** (M5) |
| L78-86 | `McpProps` 타입 (mcpServerId) | **R** (M5) — `connectionId: string` 기반으로 변경 or 유지 후 내부에서 connection 조회로 변환 |
| L339 | `const needsOptionDFirstBind = !!tool && !tool.connection_id` | **D** (M4) |
| L340 | `const saveDisabled = isPending \|\| needsOptionDFirstBind` → `isPending`만 | **R** (M4) |
| L343-346 | `if (needsOptionDFirstBind) { toast.error(...); return }` | **D** (M4) |
| L421-429 | `needsOptionDFirstBind` alert 블록 | **D** (M4) |
| L362-365 | CustomBody `findOrCreate.run(credentialId, ...)` — first-bind 활성화 시 `useUpdateTool({ id: tool.id, data: { connection_id: result.id } })` 체인 추가 | **N** (M4) |
| L456-516 | McpBody 전체 — `useUpdateMCPServer` 제거 + `useUpdateConnection`(extra_config 갱신) + `useUpdateTool`(connection_id rebind)로 재배선 | **R** (M5) |
| L469, L496 | `const updateServer = useUpdateMCPServer()` / `updateServer.mutateAsync(...)` | **D** (M5) |

### 3.5 `frontend/src/components/tool/mcp-server-rename-dialog.tsx`

**파일 존재 여부**: ✅ 존재 (L1-40 정도 파일). `ls` 결과 확인됨.

| 라인 | 심볼 | 태그 |
|------|------|------|
| 파일 전체 | `MCPServerRenameDialog` | **D** (M5 — 파일 삭제) |

**대체 UX**: connection rename은 `/connections` 페이지의 일반 PATCH로 대체. `frontend/src/components/connection/` 에 `useUpdateConnection` 기반 rename dialog가 이미 존재 (M5 UI 통합).

### 3.6 `frontend/src/components/tool/mcp-server-group-card.tsx`

| 라인 | 심볼 | 태그 |
|------|------|------|
| L29 | `import { MCPServerRenameDialog }` | **D** (M5) |
| L30 | `import { useDeleteMCPServer }` | **D** (M5) |
| L46 | `const deleteServer = useDeleteMCPServer()` | **R** (M5) — 대체 경로 결정 필요. 옵션: (a) MCP server 삭제 UI 자체를 제거 (tools 페이지의 group card가 redundant해짐 → 전체 렌더 분기 제거) (b) connection 기반으로 대체 |
| L143-151 | `<ConnectionBindingDialog type="mcp" mcpServerId={...} triggerContext="tool-edit" ... />` | **R** (M5) — `mcpServerId` → `connectionId`로 prop 변경 후 연결 |
| L152 | `<MCPServerRenameDialog server={server} ... />` | **D** (M5) |

**주의**: `tools/page.tsx` L379 `useMCPServers()` 호출 + L472-480 `mcp_server_id` 기반 grouping 로직(L472: `if (tl.type !== 'mcp' \|\| !tl.mcp_server_id) continue`)은 M5에서 connection 기반 재설계 필요. **Scope 재확인**: "기존 UX 그대로 유지" (scope creep 경고 §5와 충돌 가능) → 저커버그는 **UX 유지 = tools 페이지의 MCP 그룹 섹션 유지**를 최우선으로 하고, grouping key를 `mcp_server_id` → `connection_id` 로만 바꾸는 minimal rewrite 방침으로.

### 3.7 `frontend/src/app/tools/page.tsx`

| 라인 | 심볼 | 태그 |
|------|------|------|
| L20 | `import { useTools, useDeleteTool, useMCPServers }` → `useMCPServers` 제거 | **R** (M5) |
| L31 | `import { MCPServerGroupCard }` | **K 또는 R** (group card 유지 시) |
| L40 | `import type { MCPServerListItem, Tool }` | **R** (M5) |
| L379 | `const { data: mcpServers } = useMCPServers()` | **R** (M5) → `useConnections({ type: 'mcp' })` |
| L472-480 | `mcp_server_id` 기반 grouping | **R** (M5) → `tool.connection_id` |
| L481-495 | `filteredMCPServers` → `filteredMcpConnections` | **R** (M5) |
| L524, L631-642 | render 블록 | **R** (M5) |

→ M5 **R 작업 밀도가 예상보다 큼**. 저커버그 작업량 재견적 필요 (§6 경고).

### 3.8 i18n (messages)

`t('toast.unsupportedFirstBindM6')` / `t('custom.unsupportedFirstBindM6')` 키 제거.
- grep 히트 2건 (connection-binding-dialog.tsx L344, L427)
- messages 파일(`frontend/messages/*.json`)에서 해당 키 삭제 필요 — M4 스코프 포함.

---

## 4. BindingDialogShell 추출 분석

### 4.1 PrebuiltBody / CustomBody / McpBody 공통 패턴

| 패턴 | PrebuiltBody | CustomBody | McpBody |
|------|:---:|:---:|:---:|
| `useCredentials()` | L137 | L307 | L467 |
| `useQueryClient` | L132 | L306 | ❌ (conflict invalidate 없음) |
| `useConnections({ type, provider_name })` | L133-136 | L308-311 | L468 (type='mcp'만) |
| `const [createOpen, setCreateOpen] = useState(false)` | L140 | L314 | L471 |
| hydration 패턴 (hydrationKey + hydratedFor + setMode) | L153-161 | L323-331 | ❌ (L472 단순 초기화) |
| `isPending` | L170 | L334 | L518 |
| `handleSave()` (async, try/catch, 409 handling, toast) | L182-226 | L342-381 | L490-516 |
| `DialogHeader / DialogTitle / DialogDescription` | L230-239 | L387-393 | L522-528 |
| Credential section (label + Skeleton + CredentialSelect + configured badge) | L241-264 | L395-418 | L530-559 |
| `DialogFooter` (Cancel/Save) | L267-277 | L431-441 | L562-572 |
| `<CredentialFormDialog open/onOpenChange/onCreated/>` | L279-286 | L443-447 | L574-578 |

### 4.2 공통 Shell의 제안 시그니처

```ts
interface BindingDialogShellProps {
  title: ReactNode
  description: ReactNode
  credentials: Credential[]
  connectionsLoading?: boolean
  mode: string
  onModeChange: (v: string) => void
  onCreateRequested: () => void
  onSave: () => void | Promise<void>
  onCancel: () => void
  isPending: boolean
  saveDisabled?: boolean
  children?: ReactNode           // alert 블록 등 body별 고유 요소
  createDialogProps: { open; onOpenChange; defaultProvider?; onCreated }
  configuredBadge?: boolean      // 기본 true — MCP도 동일 패턴
}
```

### 4.3 각 body의 고유 로직 (추출 불가)

| Body | 고유 로직 |
|------|-----------|
| PrebuiltBody | `targetConnection` (explicit vs default) + `shouldCreateNew` 분기 + create/update branch + `onSaved` 후방 호환 콜백 |
| CustomBody | `currentConnection` 해석 + `findOrCreate.run()` + (M4 이후) `useUpdateTool` 체인 + first-bind 가드 alert |
| McpBody | `linkedConnections` / `sharedAcrossServers` 계산 + 이중 PATCH (server + connection) |

→ **Shell 추출은 viable**. 각 body는 ~50줄 내외로 축소 가능. **hydration 패턴은 Prebuilt/Custom에서만 사용** — Shell에 옵션으로 내재화하거나 body가 직접 소유. 전자는 추상화 비용이 크므로 **후자 권장** (body가 hydrationKey 계산 후 mode state만 Shell에 전달).

### 4.4 추출 전략 권고

**M5 최소 스코프**:
1. Shell에는 **UI chrome만** (Dialog, Header, Footer, Credential section, configured badge, CredentialFormDialog) 이관
2. hydration / save 로직은 body가 각자 소유
3. `ConnectionBindingDialog` dispatcher는 그대로 유지

→ 회귀 위험 최소 + 코드 중복 ~80줄 제거.

---

## 5. Scope Creep 경고 (저커버그/젠슨 주의사항)

다음 범위는 **M6.1 스코프 아님**. 건드리지 말 것:

### 5.1 `agent_tools.connection_id` override (M5.5)
- `backend/app/models/tool.py::AgentToolLink`에 `connection_id` 컬럼 추가 금지
- `AgentToolLink`에 새 FK 추가 금지
- PO (사티아) 승인 없이는 M5.5 프로토타입도 만들지 말 것

### 5.2 PATCH /api/tools/{id} 필드 확장 금지
- `ToolUpdate`는 **`connection_id` 단 하나**
- `name`, `description`, `parameters_schema`, `provider_name`, `is_system`, `tags` 등 어떤 필드도 추가 금지
- `extra="forbid"` Pydantic 설정 필수 (테스트에서 `unknown_field` 검증)

### 5.3 UI 리디자인 금지
- `/connections` 페이지 카드 레이아웃 변경 금지
- `/tools` 페이지 MCP 그룹 섹션 UX 유지 (grouping key만 `mcp_server_id` → `connection_id` 교체, 카드 구조/툴바/i18n 레이블은 그대로)
- BindingDialogShell 추출은 **리팩토링이지 리디자인이 아님** — 픽셀 동일성 유지

### 5.4 PREBUILT PATCH 400 원칙 건드리지 말 것
- `PATCH /api/tools/{id}`는 `tool.type in ('custom','mcp')`만 허용
- `is_system=True`의 PREBUILT 행에 `connection_id`를 심는 유혹 금지 — ADR-008 §3 위반. PREBUILT는 `(user_id, provider_name)` SOT 유지

### 5.5 테스트 정리 범위 한정
- `backend/tests/test_connection_mcp_resolve.py`는 MCP connection path 회귀 테스트 — **drop 금지**. 안의 "mcp_server" 참조(39건)는 comment/docstring에서만 나오는지 확인 후 구조 유지
- `backend/tests/test_tools.py` (32 hits)는 MCPServer CRUD 테스트 섹션만 삭제, CUSTOM tool 테스트는 유지
- `backend/tests/test_tools_router_extended.py` (10 hits)는 MCP 라우터 케이스만 삭제
- `backend/tests/integration/test_m9_pg_roundtrip.py` (5 hits)는 m9 migration round-trip — **M6.1에서 건드리지 말 것** (m9은 이미 released/applied)

---

## 6. 파일별 Delta 요약 (리뷰용)

### Backend
| 파일 | 제거 라인(추정) | 추가 라인(추정) | net |
|------|:---:|:---:|:---:|
| `alembic/versions/m13_drop_mcp_legacy.py` | — | +80 | **+80** (신규) |
| `models/tool.py` | -28 | 0 | **-28** |
| `models/__init__.py` | -2 | 0 | **-2** |
| `schemas/tool.py` | -35 | +8 (ToolUpdate) | **-27** |
| `services/tool_service.py` | -170 | +35 (update_tool) | **-135** |
| `services/credential_service.py` | -16 | 0 | **-16** |
| `services/chat_service.py` | -15 | +3 (fail-closed raise) | **-12** |
| `services/legacy_invariants.py` | 0 | +20 (m13 preflight) | **+20** |
| `routers/tools.py` | -65 | +30 (PATCH + test 재작성) | **-35** |
| `main.py` | 0 | +3 | **+3** |
| `error_codes.py` | -5 | 0 | **-5** |
| `tests/*` | -200 (추정) | +80 (PATCH tool, MCP connection test) | **-120** |
| **합계** | **~-536** | **~+259** | **~-277 LOC** |

### Frontend
| 파일 | 제거 라인(추정) | 추가 라인(추정) | net |
|------|:---:|:---:|:---:|
| `lib/types/index.ts` | -40 | +5 (ToolUpdateRequest) | **-35** |
| `lib/api/tools.ts` | -18 | +6 (update) | **-12** |
| `lib/hooks/use-tools.ts` | -50 | +15 (useUpdateTool) | **-35** |
| `components/connection/connection-binding-dialog.tsx` | -80 (McpBody 재구성 + needsOptionDFirstBind) | +40 | **-40** |
| `components/connection/binding-dialog-shell.tsx` | — | +120 | **+120** (신규) |
| `components/tool/mcp-server-rename-dialog.tsx` | -40 | 0 | **-40** (파일 삭제) |
| `components/tool/mcp-server-group-card.tsx` | -10 | +5 | **-5** |
| `app/tools/page.tsx` | -15 | +15 | **~0** (grouping key 교체) |
| `messages/*.json` | -2 keys | 0 | **-2** |
| **합계** | **~-255** | **~+206** | **~-49 LOC** |

→ **전체 리포지토리 ~-326 LOC** 순감소. single PR로 충분히 리뷰 가능 규모. 계획서 §"작업 순서 제안"의 권고대로 단일 PR 유지 가능.

---

## 7. 1-way door 결정 사항 (되돌릴 수 없음)

| 결정 | 위험도 | 완화 |
|------|:---:|------|
| `mcp_servers` 테이블 drop | 🔴 High (1-way door) | ① pre-check SQL로 dead refs = 0 확인 ② alembic round-trip (docker PG) ③ `SELECT to_regclass('mcp_servers')` NULL 확인 후 PR merge |
| `tools.mcp_server_id` FK + 컬럼 drop | 🔴 High (1-way door) | ① m12 precedent `\d tools`로 FK 이름 실측 ② pre-check: `COUNT(*) WHERE mcp_server_id IS NOT NULL AND connection_id IS NULL = 0` |
| `resolve_server_auth` 삭제 | 🟢 Low (2-way door) | git revert 가능 |
| PATCH /api/tools/{id} 신설 | 🟢 Low (2-way door) | 스키마 `extra="forbid"`로 오남용 방지. 추후 필드 확장 시 명시 승인 |
| `MCPServer` 모델/스키마 삭제 | 🔴 Medium-High | 코드 삭제는 revert 가능하나, DB drop과 paired — drop과 함께 일관성 유지 |

---

## 8. Verification Checklist (M6 통합 검증 선결)

베조스가 M6에서 수행할 최종 검증 — M2~M5 완료 후:

```bash
cd backend
uv run ruff check .                                  # PASS
uv run pytest                                        # 0 regression (baseline 624)
uv run alembic upgrade head                          # m13 적용
uv run alembic downgrade -1 && uv run alembic upgrade head  # round-trip

# 잔재 grep — 반드시 0
rg "mcp_server_id|MCPServer|resolve_server_auth|mcp-server" backend/app/
rg "mcp_server_id|MCPServer|resolve_server_auth" backend/tests/ | grep -v "m9"  # m9 round-trip 보존

cd ../frontend
pnpm lint
pnpm build

# 잔재 — 반드시 0
rg "mcp_server_id|MCPServer|updateMCPServer|useUpdateMCPServer|useMCPServers|useDeleteMCPServer|useRegisterMCPServer|mcp-server-rename" frontend/src/

# 프로덕션 pre-check (m13 실행 전)
psql $DB_URL -c "SELECT COUNT(*) FROM tools WHERE mcp_server_id IS NOT NULL AND connection_id IS NULL;"
# 기대: 0

# 배포 후
psql $DB_URL -c "\d tools"                           # mcp_server_id 없음
psql $DB_URL -c "SELECT to_regclass('mcp_servers');" # NULL
```

---

## 9. 최종 판정: 🟢 GREEN

### 근거
1. **제거 대상이 파일:라인으로 확정** — 57 backend + 53 frontend grep 히트 모두 위에서 매트릭스화.
2. **`test_mcp_connection` 재작성 경로 확립** — `chat_service` L369-376의 `resolve_env_vars(extra.env_vars, conn.credential, ...)` 패턴 재사용. 새 의존성 없음.
3. **`useUpdateTool` 네이밍 충돌 없음** — grep로 현존 훅 전수 확인.
4. **BindingDialogShell 추출 viable** — UI chrome만 이관 + hydration은 body 소유 전략. 리디자인 아닌 순수 리팩토링.
5. **1-way door 리스크는 m12 precedent로 완화됨** — pre-check SQL, round-trip, FK 실측 가이드 전부 정립.
6. **Scope creep 플래그 4건**을 §5에 명문화 → 젠슨/저커버그 가드 완료.

### 사티아 확인 없이도 M2/M4 착수 가능한 이유
- M2 (백엔드 옵션 D): `ToolUpdate` 스키마 + 단일 서비스 함수 + 단일 라우트 — 사양이 CHECKPOINT L43-54에 완전히 명시. 새로운 결정 포인트 없음.
- M4 (프론트 옵션 D): `useUpdateTool` + CustomBody first-bind 가드 제거 — 네이밍 확정, UI 변경 없음.

### YELLOW/RED로 전환될 조건
- ⚠️ **YELLOW 트리거**: `credential_service.get_usage_count` 반환 shape 변경(`mcp_server_count` 필드 제거)이 기존 `/connections` 페이지 카드 카운트 UX에 영향 주는 것으로 판명되면 — 프론트 필드 제거/fallback 합의 필요. **→ 현재 `CredentialUsage`를 쓰는 프론트 위치 grep 확인 권장 (M3 착수 전)**
- 🚫 **RED 트리거**: 프로덕션 `SELECT COUNT(*) WHERE mcp_server_id IS NOT NULL AND connection_id IS NULL > 0` 나오면 — m9 migration 재실행 필요. 현재로선 PoC 단계 / local docker 환경이라 해당 가능성 낮음.

---

## 10. M2/M4 착수 전 젠슨/저커버그에게 전달할 핵심 정보

### 젠슨(M2)
- `ToolUpdate` 위치: `backend/app/schemas/tool.py` 하단에 추가 (`class ToolUpdate(BaseModel): model_config = ConfigDict(extra="forbid"); connection_id: uuid.UUID | None = None`)
- `update_tool` 위치: `backend/app/services/tool_service.py::delete_tool` 위(L252 근처)에 배치
- PATCH 라우터: `routers/tools.py` L114의 `DELETE /{tool_id}` 바로 위 (L113)에 `PATCH /{tool_id}` 추가 — FastAPI 경로 우선순위상 `/mcp-server/*`는 아직 살아있으므로 충돌 없음 (M3에서 `/mcp-server*` 제거되어도 영향 없음)
- 검증 케이스(CHECKPOINT L51 참조): 정상 / 남의 connection 404 / 타입 불일치 422 / PREBUILT 400 / None 허용 / 시스템 도구 MCP / `extra="forbid"`로 unknown_field 422

### 저커버그(M4)
- `useUpdateTool` 추가 위치: `frontend/src/lib/hooks/use-tools.ts::useDeleteTool` 아래 (L53 근처)
- invalidate 키: `['tools']` + `['agents']` (agent tool_links가 tool.connection_id를 포함하므로)
- CustomBody 가드 제거: connection-binding-dialog.tsx L339-346, L421-429 — 단 `findOrCreate.run()` 호출 성공 후 `toolsApi.update(tool.id, { connection_id: result.id })` 체인 추가 필요
- first-bind 흐름: `tool.connection_id IS NULL` 상태에서 credential 선택 → `findOrCreate` (connection 생성) → `useUpdateTool` (tool.connection_id 바인딩) → invalidate → toast
- i18n key 제거: `toast.unsupportedFirstBindM6`, `custom.unsupportedFirstBindM6` (messages/ko.json + en.json 등 전 언어 파일)

---

**보고서 종료** — 베조스, 2026-04-24
