# M5 — ConnectionBindingDialog UX 스펙

**Owner**: 팀쿡 (Tim Cook)
**Related**: ADR-008 §3, exec-plan §4 M5 · S3
**Status**: Draft for 저커버그 구현 (S3)
**Scope**: 프론트엔드 전용. 3개 인증 다이얼로그(PREBUILT / CUSTOM / MCP)를 **단일 셸**로 수렴한다.

---

## 1. 배경 및 목표

### 1.0 본 spec은 **기존 셸의 확장** 작업이다 (신규 생성 아님)

M3에서 `frontend/src/components/connection/connection-binding-dialog.tsx` (237줄)가 **PREBUILT 전용**으로 이미 존재한다. M4에서 `PrebuiltAuthDialog`가 이 셸을 thin-wrap하도록 교체되었다. **M5의 작업은 기존 셸을 `type` discriminated union으로 확장해 CUSTOM/MCP 분기를 추가하는 것**이며, 신규 파일을 만들지 않는다.

따라서 spec §2의 Props 계약은 **기존 셸의 props와 후방 호환되어야 한다**:

- 기존 prop: `type: 'prebuilt'`, `providerName: PrebuiltProviderName`, `toolName?`, `open`, `onOpenChange`, `onSaved?(connection)`
- 신규 prop: `triggerContext`, `currentConnectionId?`, 그리고 타입 선언의 `type` 리터럴 유니온 확장
- 기존 `onSaved`는 유지하되 `onBound`를 **alias**로 추가 (같은 시그니처). M4까지의 PREBUILT 호출처(`/connections` PREBUILT 카드)는 prop 변경 없이 동작해야 한다.

### 1.1 현재 (M4 까지)

| Dialog | 파일 | 저장 경로 | 상태 |
|---|---|---|---|
| PREBUILT | `components/tool/prebuilt-auth-dialog.tsx` | M4에서 이미 `ConnectionBindingDialog(type='prebuilt')`로 thin wrapper화 완료 | ✅ wrapper |
| CUSTOM | `components/tool/custom-auth-dialog.tsx` | `useUpdateToolAuthConfig` → `tool.credential_id` | ❌ 레거시 |
| MCP | `components/tool/mcp-server-auth-dialog.tsx` | `useUpdateMCPServer` → `mcp_servers.credential_id` | ❌ 레거시 |
| `add-tool-dialog.tsx` · Custom 탭 | 인라인 구현 | M4 find-or-create 흐름으로 Connection 기반 | ✅ 부분 |
| `add-tool-dialog.tsx` · MCP 탭 | 인라인 구현 | `useRegisterMCPServer` (credential_id 직접) | ❌ 레거시 |

### 1.2 목표 (M5)

**ConnectionBindingDialog** 1개 파일이 PREBUILT / CUSTOM / MCP 세 `type`을 전부 처리한다. 기존 3개 다이얼로그는 모두 삭제 또는 thin wrapper가 된다 (드롭 판단은 S1 베조스 결과에 따름).

디자인 원칙 (팀쿡):
1. **Simplicity** — 공통 surface(헤더/본문/푸터) + `type`별 섹션만 분기. 3개 UI로 느껴지지 않는 **하나의 일관된 경험**.
2. **Minimal Steps** — "기존 연결이 있으면 고른다, 없으면 만든다" 두 동선을 두 번 클릭 안에.
3. **Accessibility by Default** — focus trap, ESC, role=alert를 spec에 명시.
4. **Explicit Feedback** — 상태 전이(조회/저장/성공/실패)마다 **시각 신호 + 스크린리더 안내**.

---

## 2. Props 계약

```ts
interface ConnectionBindingDialogProps {
  /** 현재 열려 있는가 */
  open: boolean
  onOpenChange: (open: boolean) => void

  /** 바인딩할 Connection 타입 */
  type: 'prebuilt' | 'custom' | 'mcp'

  /**
   * provider_name. PREBUILT는 필수·고정(PrebuiltProviderName),
   * CUSTOM은 항상 'custom_api_key'로 고정,
   * MCP는 optional (사용자가 새 server 정의 시 body의 name으로 파생).
   */
  providerName?: string

  /**
   * Dialog 진입 맥락. i18n 카피/CTA 레이블/완료 동작이 달라진다.
   *   - 'tool-create'     : AddToolDialog 내부 (MCP 탭)에서 호출 — 완료 시 tool 등록까지 이어짐
   *   - 'tool-edit'       : 에이전트 도구 탭 / tools/page에서 호출 — 이미 존재하는 tool의 credential 변경
   *   - 'standalone'      : /connections 페이지에서 호출 — 단순히 Connection CRUD
   */
  triggerContext: 'tool-create' | 'tool-edit' | 'standalone'

  /**
   * tool-edit 맥락에서만 의미 있음. 헤더 타이틀·설명에 이름이 표시된다.
   * PREBUILT의 경우 provider 한국어 라벨을 폴백으로 사용.
   */
  toolName?: string

  /**
   * tool-edit 맥락에서만 의미 있음. CUSTOM/MCP의 "현재 bound된 connection"을 하이드레이트.
   * undefined면 default connection을 찾아 하이드레이트.
   */
  currentConnectionId?: string

  /**
   * 저장 성공 시 콜백. type별로 payload shape가 다르다 (discriminated result).
   *
   * - type='prebuilt' | 'custom'  → { kind: 'connection', connection }
   * - type='mcp'                  → { kind: 'mcp-credential', serverId, credentialId }
   *   (M5에서는 mcp_servers.credential_id PATCH만 수행. Connection 엔티티 전환은 M6.)
   *
   * 호출 측은 이 payload로 다음 동작(tool POST / refetch / close)을 결정한다.
   */
  onBound?: (result: ConnectionBindingResult) => void

  /**
   * 후방 호환 alias. 기존 PREBUILT 호출처(M4)에서 `onSaved(connection)`을 그대로
   * 사용하므로, `type='prebuilt' | 'custom'`에 한해 Connection 단일 인자로 호출되는
   * 구식 콜백을 유지한다. `onBound`가 주어지면 `onSaved`는 무시.
   */
  onSaved?: (connection: Connection) => void

  /**
   * MCP 전용. `type='mcp'` + `triggerContext='tool-edit'` 에서 필수 —
   * 편집 대상 mcp_server row의 id. PATCH 타겟이 된다.
   * standalone MCP "연결 추가"는 본 스펙에서 **지원하지 않는다** (§4.3 참조).
   */
  mcpServerId?: string
}

type ConnectionBindingResult =
  | { kind: 'connection'; connection: Connection }
  | { kind: 'mcp-credential'; serverId: string; credentialId: string | null }
```

**설계 메모**
- PREBUILT 모드에서 `providerName`은 반드시 `PrebuiltProviderName` narrow 타입이어야 한다(저커버그: TS 레벨에서 runtime guard + 조건부 타입 둘 다 사용).
- **저장 동작을 호출 측이 선택하지 않는다.** 다이얼로그 내부에서 Connection upsert까지 마친 뒤 `onBound`로 결과를 넘긴다. 호출 측은 "그 다음 무엇을 할지"만 책임진다 (tool POST / PATCH / 닫기).
- `open`/`onOpenChange`는 uncontrolled 모드 금지. **항상 controlled** — 상태 주도권은 호출 측.

---

## 3. 상태 머신

```
          ┌──────────┐
          │   idle   │  (open=false)
          └────┬─────┘
        open=true │
          ┌──────▼──────────┐
          │    loading      │  useConnections({type, provider_name}) 진행 중
          │  (skeleton UI)  │  useCredentials도 병렬 조회
          └──────┬──────────┘
                 │ load done
        ┌────────┴────────┐
        │                 │
┌───────▼─────────┐  ┌────▼────────────────┐
│ connection_     │  │  credential_form    │  (no connection yet OR user chose "새로 만들기")
│ select          │  │  (inline sub-panel) │
│ (default + list)│  └────────┬────────────┘
└───────┬─────────┘           │ credential 저장됨
        │ 저장                │
        └────────┬────────────┘
                 │
          ┌──────▼──────┐
          │   binding   │  createConnection / updateConnection 진행 중
          │  (spinner)  │
          └──────┬──────┘
        ┌────────┴────────┐
   성공 │                 │ 실패
┌──────▼─────┐      ┌─────▼──────────────┐
│  success   │      │      error         │
│ toast→onBound│     │ role=alert inline  │
│ dialog close │     │ + retry available  │
└────────────┘      └────────────────────┘
```

### 전이 규칙

| 전이 | 트리거 | 비고 |
|---|---|---|
| idle → loading | `open=true` | PREBUILT/CUSTOM/MCP 모두 연결 목록 조회 |
| loading → connection_select | 기존 active connection ≥1개 | default가 있으면 해당 선택 상태로 하이드레이트 |
| loading → credential_form (스킵) | 기존 연결 0개 AND PREBUILT | "바로 새로 만들기" — 중간 선택 화면 건너뛰기 |
| connection_select → credential_form | 사용자가 "새 연결 만들기" 클릭 | inline panel 오픈 (새 dialog 금지 — nested dialog 회피) |
| credential_form → binding | 사용자가 Credential 저장 | Credential 생성 → 자동으로 Connection upsert 트리거 |
| binding → success | 2xx | `toast.success` + `onBound(connection)` + dialog close |
| binding → error | 4xx/5xx | 409는 즉시 `invalidateQueries` + "다시 시도" 토스트, 나머지는 inline `role=alert` |

### 409 경합 처리

M3/M4와 동일:
1. `['connections', type, providerName]` scope + 전체 `['connections']` prefix를 invalidate.
2. `role=alert`로 "다른 세션에서 기본 연결이 변경되었습니다. 최신 상태로 다시 시도해주세요." 렌더.
3. Dialog는 **열린 채 유지** — 사용자가 최신 목록을 보고 재선택.

---

## 4. Type 별 분기

공통 shell은 DialogHeader · DialogBody · DialogFooter 세 섹션. 본문만 `type`별로 다르다.

### 4.1 PREBUILT (`type='prebuilt'`)

이미 M4에 구현된 흐름을 **유지**. M5에서는 "CUSTOM/MCP도 이 셸로 흡수" 작업이 핵심이므로, PREBUILT 섹션은 코드·UX 모두 회귀 금지.

```
┌─ DialogHeader ─────────────────────────────────┐
│ [KeyIcon] {toolName ?? t('provider.<key>')} 연결 │
│ 이 도구가 사용할 credential을 선택합니다.           │
└─────────────────────────────────────────────────┘
┌─ DialogBody ───────────────────────────────────┐
│ [LinkIcon] 연결 (label)                          │
│ ┌───────────────────────────────────────────┐   │
│ │ CredentialSelect                          │   │
│ │  · 인증 없음 / 기존 credentials / 새로 만들기 │   │
│ └───────────────────────────────────────────┘   │
│                                                 │
│ (선택됨) [CheckIcon] 연결이 설정되어 있습니다       │
└─────────────────────────────────────────────────┘
┌─ DialogFooter ─────────────────────────────────┐
│               [취소]  [저장]                      │
└─────────────────────────────────────────────────┘
```

**조건별 UX**
- `provider_name`을 filter key로 `useCredentials()` 결과를 narrow → 옆 provider의 credential은 보이지 않는다 (M3 회귀 방지).
- "새로 만들기"는 `CredentialFormDialog`를 nested로 오픈하되 `defaultProvider={providerName}`로 provider 고정.

### 4.2 CUSTOM (`type='custom'`, providerName='custom_api_key')

M4 `add-tool-dialog`의 custom 탭 로직(find-or-create)을 **셸 안으로 흡수**한다.

#### 4.2.a Bridge override 보존 정책 (M5 결정)

M4에서 사용자가 만든 CUSTOM tool 중 `tool.credential_id != connection.credential_id`인 "bridge override" row가 존재할 수 있다 (`add-tool-dialog.tsx:153-160` 주석 참조). **M5에서는 이 row를 보존**한다. 결정의 근거와 PATCH 방향:

| 상태 | 현재 값 | 저장 시 PATCH 방향 |
|---|---|---|
| 정상 | `tool.credential_id == connection.credential_id` | `connection.credential_id`만 PATCH (`PATCH /api/connections/{id}`). `tool.credential_id`는 건드리지 않는다. 서버가 자동으로 derive. |
| Bridge override | `tool.credential_id != connection.credential_id` | 기본 저장 시에도 **`tool.credential_id`는 건드리지 않고** `connection.credential_id`만 PATCH. 즉 override 상태는 M6까지 유지된다. |

UI 상 override를 "깨뜨리는" 동선은 사용자가 명시적으로 선택할 때만 제공:

- tool-edit 맥락 진입 시 `tool.credential_id != connection.credential_id` 감지되면, DialogBody 상단에 **inline 경고 배너** + **"공통 connection으로 복귀" 버튼** (옵션). 이 버튼을 눌렀을 때만 `tool.credential_id`를 clear하는 PATCH를 함께 발사 (호출측 책임 — `onBound` result의 `kind: 'connection'`에 `overrideCleared: true` flag 동봉).
- 기본 경로(경고 읽고 그냥 저장)는 override를 보존.
- override clear는 **M6 legacy drop에서 일괄 처리**되므로, M5의 UI에서도 기본값은 "보존" 방향으로 기운다.

```
┌─ DialogHeader ─────────────────────────────────┐
│ [KeyIcon] {toolName} 연결                         │
│ 이 도구가 호출할 외부 API의 인증 정보를 선택합니다.  │
└─────────────────────────────────────────────────┘
┌─ DialogBody ───────────────────────────────────┐
│ [LinkIcon] 연결                                  │
│ ┌───────────────────────────────────────────┐   │
│ │ CredentialSelect (모든 credential 노출)     │   │
│ └───────────────────────────────────────────┘   │
│                                                 │
│ (선택됨 AND N>1 tool 재사용시)                    │
│ [InfoIcon] 이 credential은 {n}개 도구에서 재사용됩니다. │
└─────────────────────────────────────────────────┘
┌─ DialogFooter ─────────────────────────────────┐
│               [취소]  [저장]                      │
└─────────────────────────────────────────────────┘
```

**조건별 UX**
- CredentialSelect의 `credentials` prop은 PREBUILT와 달리 **narrow하지 않는다** (사용자가 자유롭게 고를 수 있도록).
- 저장 시 내부 동작: `scopeKey({type:'custom', provider_name:'custom_api_key'})` 캐시에서 `credential_id` 일치하는 기존 connection을 찾고, 없으면 `createConnection` — find-or-create. M4 `resolveCustomConnectionId` 로직을 셸 내부 util로 승격.
- **bridge override 절대 제거 금지** — `triggerContext='tool-edit'`이고 현재 tool의 `credential_id != connection.credential_id` 상태라면, UI에 **"이 도구는 현재 직접 지정된 credential을 쓰고 있습니다"** 경고 배지를 띄우고, `onBound` 호출 전에 사용자가 인지한다. 실제 `tool.credential_id` 업데이트 여부는 호출 측이 판단 (M6까지 유지).

### 4.3 MCP (`type='mcp'`)

#### 4.3.a 스코프 결정 (M5) — credential binding **만** 담당

MCP는 PREBUILT/CUSTOM과 **비대칭**이다: server 엔티티(`mcp_servers` row — URL, transport, auth_type)가 선행하고 credential은 **server의 2차 속성**이다. exec-plan §4 M5는 "server config + credential"까지 단일 셸에 담는 방향을 언급했으나, 베조스 S1 분석과 합의하여 **M5에서는 스코프를 좁힌다**:

- **M5 결정**: `type='mcp'` 셸은 **credential 바인딩 교체만** 담당. 기존 `mcp_servers` row에 대해 `useUpdateMCPServer({ credential_id })` 호출로 귀결.
- server **생성**은 `add-tool-dialog` MCP 탭에 그대로 둔다 (UX·백엔드 경로 모두 유지). 다만 MCP 탭 내부의 "새 credential 만들기" CTA만 `CredentialFormDialog`로 진입 — 이미 M4 상태와 동일, 변경 없음.
- server **메타데이터(URL, transport, 이름) 편집**은 기존 `mcp-server-rename-dialog` 등 서버-단 컴포넌트가 담당 (M5 스코프 외). M6에서 Connection 엔티티(type='mcp') 통합 시 `extra_config` 편집 UX를 재설계.
- 따라서 M5의 `type='mcp'` 셸은 **`mcpServerId` + `credentialId`** 만 다룬다. Connection 엔티티를 생성/업데이트하지 않는다 — backend `mcp_servers` 테이블이 M6까지 소스 오브 트루스로 남기 때문.

근거 (Simplicity 원칙):
1. 단일 셸이 3 type의 이질적 저장 경로를 전부 내재화하면 오히려 복잡도 폭발.
2. server metadata를 편집하는 **별도 동선이 이미 존재**하는데(add-tool-dialog / rename-dialog) 중복 UI를 만들 이유 없음.
3. F 흡수 검증의 기준은 "3 AuthDialog 호출처 0"인데, `MCPServerAuthDialog`의 실제 역할이 **credential binding** 뿐이므로 그 역할만 수렴하면 체크리스트 통과.

#### 4.3.b 와이어프레임 (credential binding 전용)

```
┌─ DialogHeader ─────────────────────────────────┐
│ [KeyIcon] {serverName} 연결                       │
│ 이 MCP 서버가 사용할 credential을 선택하세요.        │
└─────────────────────────────────────────────────┘
┌─ DialogBody ───────────────────────────────────┐
│ [LinkIcon] 연결                                  │
│ ┌───────────────────────────────────────────┐   │
│ │ CredentialSelect                          │   │
│ │  · 인증 없음 / 기존 credentials / 새로 만들기 │   │
│ └───────────────────────────────────────────┘   │
│                                                 │
│ (선택됨) [CheckIcon] 연결이 설정되어 있습니다       │
└─────────────────────────────────────────────────┘
┌─ DialogFooter ─────────────────────────────────┐
│               [취소]  [저장]                      │
└─────────────────────────────────────────────────┘
```

본문 레이아웃은 PREBUILT/CUSTOM과 **거의 동일한 CredentialSelect-only**. type별 분기 비용을 최소화한다.

#### 4.3.c 저장 경로

```ts
// Pseudocode
const credentialId = mode === CREDENTIAL_NONE ? null : mode
await updateMCPServer.mutateAsync({
  id: mcpServerId,
  data: { credential_id: credentialId },
})
onBound?.({ kind: 'mcp-credential', serverId: mcpServerId, credentialId })
```

- `useConnections({type:'mcp'})`는 호출하지 않는다 (M5에서 MCP connection 엔티티를 만들지 않기 때문). 대신 `useCredentials()`만 조회.
- `mcpServerId` 없이 `type='mcp'`로 셸을 여는 경우는 개발 오류로 취급 — 렌더 시점에 `console.error` 후 inline `role=alert`.
- **`/connections` MCP 섹션의 "연결 추가" CTA는 본 셸을 열지 않는다** (§8 호출처 표에서 별도 처리 — add-tool-dialog 진입 또는 서버-선행 선택 UX).

---

## 5. 공통 UX 규칙

### 5.1 로딩 상태
- 초기 `useConnections` 로딩 중에는 CredentialSelect 자리에 `<Skeleton className="h-9 w-full" />`.
- 저장(binding) 중에는 [저장] 버튼에 `<Loader2Icon className="animate-spin" />` + `disabled`.

### 5.2 에러 UI
- Toast (`sonner`): 네트워크/서버 오류(500, 일반 4xx)
- Inline `role=alert`: 422 validation 오류(예: MCP URL 형식 오류, env_vars 템플릿 규칙 위반). 필드 바로 아래, `text-sm text-destructive`.
- 409: 본문 상단 `role=alert` 배너 + "다시 시도" 안내. Dialog 닫지 않음.

### 5.3 성공 UI
- `toast.success(t('toast.saved'))`
- `onBound(connection)` 호출
- 300ms 이내에 dialog close (애니메이션 겹침 없이)

### 5.4 빈 상태 (connection 0개, credential 0개)
- PREBUILT: 바로 `credential_form` 상태로 진입. "아직 {provider} credential이 없습니다. 새로 만들어 주세요" 카피.
- CUSTOM: CredentialSelect 자체가 빈 목록 + "새 연결 만들기" 버튼을 노출. 추가 카피 불필요.
- MCP: Section A 폼을 그대로 표시 (빈 상태 = 첫 등록의 기본 상태).

---

## 6. 접근성 (WCAG 2.1 AA)

구현 시 누락되기 쉬운 항목을 명시한다.

- [ ] **Focus trap**: shadcn `Dialog`가 기본 제공. CredentialFormDialog가 nested로 열릴 때 focus가 그 안으로 이동하고 닫히면 원래 Dialog 첫 필드로 복귀.
- [ ] **ESC 닫기**: Radix Dialog 기본. 단, `binding` 상태에서는 ESC/outside click으로 close 되지 않도록 `onOpenChange` 가드.
- [ ] **키보드 네비**: Tab 순서 = Header → CredentialSelect → (보조 버튼들) → [취소] → [저장]. MCP는 Section A 필드 → Section B → Section C(접힘 해제 시) → Footer.
- [ ] **aria-live**: `role=alert` 에러 배너 (assertive). 저장 중 로딩 spinner 주변에 `aria-busy="true"`.
- [ ] **라벨**: 모든 Input에 `<label htmlFor>` 연결. Select/Combobox는 shadcn 기본 `aria-labelledby` 사용.
- [ ] **색 대비**: 에러 텍스트(`text-destructive`)와 성공 텍스트(`text-emerald-600`)는 다크/라이트 모두 4.5:1 이상 (기존 디자인 토큰 유지).
- [ ] **모션**: `prefers-reduced-motion` 존중 — Loader2Icon `animate-spin`은 사용자 설정에 따라 정지.

---

## 7. i18n 키 네이밍

기존 `connections.bindingDialog.*`를 **type별 sub-namespace**로 확장. 기존 키는 호환을 위해 유지하되, 신규 키만 사용한다.

```jsonc
"connections": {
  "bindingDialog": {
    // 공통 (모든 type)
    "save": "저장",
    "cancel": "취소",
    "configured": "연결이 설정되어 있습니다",
    "loading": "연결 정보를 불러오는 중…",
    "toast": {
      "saved": "연결이 저장되었습니다",
      "saveFailed": "연결 저장에 실패했습니다",
      "conflictRetry": "다른 세션에서 기본 연결이 변경되었습니다. 최신 상태로 다시 시도해주세요."
    },

    // PREBUILT
    "prebuilt": {
      "title": "{name} 연결",
      "description": "이 도구가 사용할 credential을 선택하세요.",
      "emptyCredential": "아직 {provider} credential이 없습니다. 새로 만들어 주세요."
    },

    // CUSTOM
    "custom": {
      "title": "{toolName} 연결",
      "description": "이 도구가 호출할 외부 API의 인증 정보를 선택하세요.",
      "reusedBadge": "{count}개 도구에서 재사용됨",
      "bridgeOverrideWarning": "이 도구는 현재 다른 credential을 직접 지정해 사용 중입니다. 저장하면 공통 connection으로 돌아갑니다."
    },

    // MCP
    "mcp": {
      "title": "MCP 서버 연결",
      "description": "연결할 MCP 서버 정보를 입력하고 credential을 지정하세요.",
      "sectionServer": "서버 기본 정보",
      "sectionCredential": "Credential",
      "sectionAdvanced": "고급 설정",
      "displayName": "표시 이름",
      "url": "서버 URL",
      "transport": "Transport",
      "authType": "인증 유형",
      "timeout": "Timeout (초)",
      "headers": "Headers",
      "envVars": "환경 변수",
      "envVarTemplateHint": "값은 ${credential.<field_name>} 템플릿만 허용됩니다.",
      "envVarTemplateError": "평문 값은 허용되지 않습니다. credential 참조 템플릿을 사용하세요."
    }
  }
}
```

**충돌 방지**: 기존 `tool.addDialog.*`, `tool.authDialog.provider.*`, `tool.customAuth.*`, `tool.mcpServer.auth.*` 키와 중복되지 않도록 반드시 `connections.bindingDialog.<type>.*` 하위에 둔다.

---

## 8. 호출 측 시그니처 변경 (S3 구현 메모)

| 호출 측 | Before | After |
|---|---|---|
| `/connections` PREBUILT 카드 | `ConnectionBindingDialog(type='prebuilt', providerName, toolName, open, onOpenChange)` | **변경 없음** (M4와 동일) |
| `/connections` CUSTOM 섹션 "연결 추가" (신규) | — | `ConnectionBindingDialog(type='custom', providerName='custom_api_key', triggerContext='standalone', onBound)` |
| `/connections` MCP 섹션 "연결 추가" (신규) | — | **본 셸을 열지 않는다**. `add-tool-dialog` MCP 탭을 열거나, "먼저 서버를 등록하세요" 안내 → 서버 등록 후 자동으로 `type='mcp'` 셸이 credential 지정을 요구. 구체 UX는 `m5-connections-page-redesign-spec.md` §3.3 참조 |
| `tools/page.tsx` (에이전트 도구 편집) CUSTOM | `<CustomAuthDialog tool trigger>` | `<ConnectionBindingDialog type='custom' triggerContext='tool-edit' toolName={tool.name} currentConnectionId={tool.connection_id} onBound={...}>` + trigger wrapper |
| `components/tool/mcp-server-group-card.tsx` (MCP 서버 "인증" 버튼) | `<MCPServerAuthDialog server open onOpenChange>` | `<ConnectionBindingDialog type='mcp' triggerContext='tool-edit' mcpServerId={server.id} toolName={server.name} onBound={...}>` — 내부는 `useUpdateMCPServer({credential_id})` |
| `add-tool-dialog.tsx` MCP 탭 | 인라인 폼 + `useRegisterMCPServer` | **변경 최소화** — 서버 생성 폼(name/url)과 `useRegisterMCPServer` 호출은 유지. "새 credential 만들기" CTA는 기존 `CredentialFormDialog` 그대로 (M4 상태 유지). ConnectionBindingDialog로 감싸지 않는다 (§4.3.a 결정) |
| `add-tool-dialog.tsx` Custom 탭 | 인라인 폼 (M4 find-or-create) | **변경 없음** (M4 완료 — 드라이브-바이 금지) |

**wrapper 파일 처리**:
- `prebuilt-auth-dialog.tsx`: M4 현재 이미 thin wrapper. S3에서 **호출처가 없어지면 파일 자체 삭제** 판단은 베조스 S1 결과 반영.
- `custom-auth-dialog.tsx`, `mcp-server-auth-dialog.tsx`: S3에서 삭제 또는 thin wrapper. 바깥에서 `import` 잔재가 있으면 모두 치환.

---

## 9. 와이어프레임 (ASCII)

### PREBUILT — 기존 연결 있는 상태

```
┌──────────────────────────────── 🔑 Naver 검색 연결 ─────┐
│ 이 도구가 사용할 credential을 선택하세요.                 │
│                                                         │
│ 🔗 연결                                                  │
│ ┌─────────────────────────────────────────────────────┐ │
│ │ 내 Naver API 키                          ▾          │ │
│ └─────────────────────────────────────────────────────┘ │
│                                                         │
│ ✔ 연결이 설정되어 있습니다                                │
│                                                         │
│                                    [취소]  [저장]        │
└─────────────────────────────────────────────────────────┘
```

### CUSTOM — tool-edit 맥락, bridge override 경고

```
┌──────────────────────────────── 🔑 Weather API 연결 ────┐
│ 이 도구가 호출할 외부 API의 인증 정보를 선택하세요.        │
│                                                         │
│ ⚠ 이 도구는 현재 다른 credential을 직접 지정해 사용 중입니다. │
│   저장하면 공통 connection으로 돌아갑니다.                 │
│                                                         │
│ 🔗 연결                                                  │
│ ┌─────────────────────────────────────────────────────┐ │
│ │ 주식 API 키 (3개 도구에서 사용 중)          ▾        │ │
│ └─────────────────────────────────────────────────────┘ │
│                                                         │
│                                    [취소]  [저장]        │
└─────────────────────────────────────────────────────────┘
```

### MCP — tool-create 맥락, 고급 접힘

```
┌────────────────────────────────── 🖥 MCP 서버 연결 ──────┐
│ 연결할 MCP 서버 정보를 입력하고 credential을 지정하세요. │
│                                                         │
│ ▸ 서버 기본 정보                                          │
│    표시 이름  [                          ]               │
│    서버 URL  [https://...                ]              │
│    Transport ( http )  ( stdio )                         │
│    인증 유형 ( none | bearer | api_key | oauth2 | basic )│
│                                                         │
│ ▸ Credential  (인증 유형 ≠ none)                         │
│    🔗 연결 [새 연결 만들기…          ▾]                  │
│                                                         │
│ ▸ 고급 설정 (접힘)    ▸                                  │
│                                                         │
│                                    [취소]  [등록]        │
└─────────────────────────────────────────────────────────┘
```

---

## 10. 수용 기준 (S3 검증용)

- [ ] 하나의 `ConnectionBindingDialog` 파일이 3개 `type`을 모두 처리한다.
- [ ] M4 PREBUILT 시나리오(Naver, Google 4종) 회귀 없음 — 스크린샷 동일성 수동 확인.
- [ ] CUSTOM `tool-edit`에서 find-or-create 동작이 기존 `add-tool-dialog` custom 탭과 동등.
- [ ] MCP `tool-create`에서 기존 `add-tool-dialog` MCP 탭의 "서버 등록 + tool 디스커버리" 동선 회귀 없음.
- [ ] 409 경합 시 dialog 유지 + 토스트 + invalidate 동작.
- [ ] Focus trap / ESC / aria-live / prefers-reduced-motion 점검.
- [ ] i18n 신규 키 `connections.bindingDialog.{prebuilt|custom|mcp}.*` 추가, 기존 `tool.addDialog.*` 키와 충돌 없음.
- [ ] 3개 레거시 dialog 파일이 삭제되거나 thin wrapper만 남아, `rg "AuthDialog" frontend/src/components/tool/`에 신규 셸 호출만 보임.

---

## 11. 오픈 이슈 / M5 스코프 외

- `agent_tools.connection_id` override UI — **M5.5** (별도 worktree).
- `tool.credential_id` / `tool.auth_config` / `agent_tools.config` drop — **M6**.
- MCP `mcp_servers` → Connection 엔티티 흡수, `extra_config` 편집 UX (headers / env_vars / URL / transport) — **M6**. M5는 credential binding만 담당.
- CUSTOM bridge override(`tool.credential_id != connection.credential_id`)의 일괄 정리 — **M6** legacy drop 시.

---

## 12. 저커버그 S3 구현 시 주의 (회귀 위험 조기 경보)

### 12.1 `tools/page.tsx` `getAuthStatus` 회귀 (베조스 S1 §7-4 Bezos "?")

`app/tools/page.tsx`에는 Custom tool의 "configured" 여부를 **`tool.credential_id`** 로 판정하는 `getAuthStatus` 로직이 존재할 가능성이 높다 (M5 스코프에서 베조스가 미열람). CustomAuthDialog → ConnectionBindingDialog(type='custom') 교체 **후**에도 UI 배지가 "미설정"으로 잘못 표시되는 회귀가 발생할 수 있다.

**구현 시 체크리스트**:
- [ ] 교체 전 `rg "getAuthStatus|credential_id" frontend/src/app/tools/` 로 판정 로직 위치 확인.
- [ ] 판정 기준이 `tool.credential_id` 단독이라면, **(a) 기존 bridge override 보존 원칙상 `tool.credential_id`는 계속 채워져 있으므로 회귀 없음** — ConnectionBindingDialog가 connection.credential_id만 PATCH하고 tool.credential_id를 건드리지 않기 때문. 이 경우 변경 없이 통과.
- [ ] 반대로 신규 생성 CUSTOM tool 중 `tool.credential_id IS NULL`인 row가 있다면(find-or-create에서 connection_id만 채우고 credential_id는 비우는 케이스 — `add-tool-dialog.tsx:157-160` bridge 전송 라인 참조), 판정 로직을 `tool.connection_id` 기반으로 확장해야 한다.
- [ ] **수동 E2E (S5 베조스)**: M4 bridge row / 신규 M5 row / legacy-only row 3종에서 도구 카드 "configured" 배지가 올바르게 뜨는지 확인.

### 12.2 기존 PREBUILT 호출처 회귀 방지

M4에서 `ConnectionBindingDialog(type='prebuilt')`가 이미 운영 중. `type` 리터럴 유니온 확장 시 **TypeScript narrowing**이 깨지지 않도록 체크:

```ts
if (type === 'prebuilt' && providerName) {
  // providerName은 여기서 PrebuiltProviderName으로 narrow되어야 한다
}
```

- [ ] `isPrebuiltProviderName()` guard (`lib/types/index.ts:255`) 유지.
- [ ] `useConnections({type, provider_name})` 호출은 `type='mcp'`일 때 **스킵** — MCP connection 엔티티 미사용 (§4.3.c).

### 12.3 i18n 충돌 방지 자동 검증

```bash
# 신규 키가 기존 네임스페이스를 침범하지 않는지 검증
jq 'paths(scalars) | map(tostring) | join(".")' frontend/messages/ko.json | sort | uniq -d
# 기대: 출력 없음 (중복 키 없음)
```

### 12.4 `components/tool/mcp-server-group-card.tsx` 호출부

이 파일이 `MCPServerAuthDialog`를 호출하는 유일한 곳이다 (베조스 S1 §2.2). ConnectionBindingDialog 교체 후에도 **카드의 "인증" 버튼 클릭 경험**이 동일해야 한다 — open state 관리 방식(trigger vs controlled)이 다르므로 trigger prop 대신 `open/onOpenChange`로 전환 필요.
