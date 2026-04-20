# M5 — /connections 페이지 Connection 중심 재편 스펙

**Owner**: 팀쿡 (Tim Cook)
**Related**: ADR-008, exec-plan §4 M5 · S4, `m5-connection-binding-dialog-spec.md`
**Status**: Draft for 저커버그 구현 (S4)
**Scope**: 프론트엔드 전용. `/connections` 페이지를 **Connection 1급** 구조로 전면 재편. Credential 카드는 제거, Credential은 Connection 상세 안에서만 노출.

---

## 1. 현재 구조 (M4 상태)

**파일**: `frontend/src/app/connections/page.tsx`

```
PageHeader "연결 관리"
├─ Search + Type Filter + [연결 추가] (= Credential 생성)
├─ CredentialCard 목록            ← Credential을 1급으로 노출 (기존)
└─ PrebuiltConnectionSection      ← M3에서 추가된 PREBUILT provider별 default 섹션
```

**문제**
1. "연결"과 "Credential"이 같은 페이지에서 두 개의 개념으로 혼재 — 사용자가 "뭐가 연결이고 뭐가 인증 정보인지" 구분 못함.
2. CUSTOM 섹션이 없다 — CUSTOM connection은 `/tools` 에서만 find-or-create로 간접 생성됨. 이미 생성된 CUSTOM connection을 **이 페이지에서 확인/수정/삭제할 수단이 없음**.
3. MCP 섹션도 없다 — MCP connection은 `/tools`에서 server 등록 시 간접 생성됨. 독립 관리 불가.
4. 개념 모델이 ADR-008과 어긋남 — ADR-008은 Connection을 1급, Credential을 Connection에 매달린 하위 개념으로 정의.

---

## 2. 재편 후 구조 (M5 목표)

```
PageHeader "연결 관리"
├─ (선택) 전역 필터 / 검색
│
├─ Section: PREBUILT
│    ├─ provider별 서브 그룹 (Naver, Google Search, Google Chat, Google Workspace)
│    │   └─ Connection Card 목록 (provider별 0~N개, default 배지)
│    └─ "연결 추가" CTA (provider 선택 prompt)
│
├─ Section: CUSTOM
│    ├─ Connection Card 목록
│    └─ "연결 추가" CTA
│
└─ Section: MCP
     ├─ Connection Card 목록
     └─ "연결 추가" CTA
```

**원칙**
- Credential 카드/리스트 없음. **Credential은 Connection 상세 drawer 안에서만 노출**한다.
- 각 섹션 헤더에 "연결 추가" CTA — `ConnectionBindingDialog`를 `triggerContext='standalone'`으로 오픈.
- Credential CRUD API(`lib/hooks/use-credentials.ts`)는 **삭제하지 않고 유지**한다. 내부 호출(ConnectionBindingDialog → CredentialFormDialog)에서 계속 사용.

---

## 3. 섹션 상세

### 3.1 Section: PREBUILT

#### 레이아웃

```
┌─ PREBUILT 연결 ─────────────────────────────────────────┐
│ 시스템 도구(Naver, Google 등)에 사용할 API 키를 관리합니다. │
│                                                        │
│ ┌─ Naver ──────────────────────── [+ 연결 추가] ───┐    │
│ │ ┌ [ConnectionCard] 내 Naver 키     [기본]  [상세]┐│    │
│ │ └──────────────────────────────────────────────┘│    │
│ │ ┌ [ConnectionCard] 회사 Naver 키          [상세]┐│    │
│ │ └──────────────────────────────────────────────┘│    │
│ └──────────────────────────────────────────────────┘    │
│                                                        │
│ ┌─ Google Search ─────────────── [+ 연결 추가] ───┐    │
│ │  (아직 등록된 연결이 없습니다.)                     │    │
│ └──────────────────────────────────────────────────┘    │
│                                                        │
│ ... Google Chat / Google Workspace 동일 구조            │
└────────────────────────────────────────────────────────┘
```

#### 데이터
- `useConnections({ type: 'prebuilt' })` 한 번 호출 → client-side groupBy `provider_name`.
- provider 순서: `['naver', 'google_search', 'google_chat', 'google_workspace']` 고정 (ADR-008 credential_registry 순).
- 빈 provider 그룹도 접힌 상태로 노출 — 사용자가 "어떤 provider가 있는지"를 학습하도록.

#### "연결 추가" CTA
- `ConnectionBindingDialog(type='prebuilt', providerName=<해당 그룹>, triggerContext='standalone')` 오픈.
- Provider별 그룹 헤더에 붙어 있으므로 providerName이 고정된 상태로 열림.

### 3.2 Section: CUSTOM

```
┌─ CUSTOM 연결 ─────────────────────── [+ 연결 추가] ──┐
│ 사용자 정의 도구에서 재사용할 API 키를 관리합니다.       │
│                                                       │
│ ┌ [ConnectionCard] 내 외부 API 키    [3개 도구 사용] ┐ │
│ └─────────────────────────────────────────────────┘ │
│ ┌ [ConnectionCard] Weather API 키    [1개 도구 사용] ┐ │
│ └─────────────────────────────────────────────────┘ │
└───────────────────────────────────────────────────────┘
```

#### 데이터
- `useConnections({ type: 'custom' })`.
- provider_name은 항상 `custom_api_key` (CUSTOM의 유일한 provider) — 하위 그룹 없이 flat 리스트.

#### "연결 추가" CTA
- `ConnectionBindingDialog(type='custom', providerName='custom_api_key', triggerContext='standalone')` 오픈.
- 저장 시 find-or-create. 동일 credential 기반이면 기존 connection 재사용.

### 3.3 Section: MCP

```
┌─ MCP 연결 ────────────────────────── [+ 연결 추가] ──┐
│ MCP 서버에 연결해 추가 도구를 가져옵니다.              │
│                                                       │
│ ┌ [ConnectionCard] Notion MCP  http · bearer  [상세]┐│
│ └─────────────────────────────────────────────────┘ │
│ ┌ [ConnectionCard] Linear MCP  http · api_key [상세]┐│
│ └─────────────────────────────────────────────────┘ │
└───────────────────────────────────────────────────────┘
```

#### 데이터
- `useConnections({ type: 'mcp' })`.
- 서브 그룹 없음 (provider_name은 사용자가 지정).

#### "연결 추가" CTA (MCP 섹션)

**결정 (M5)**: 이 CTA는 `ConnectionBindingDialog(type='mcp')`를 열지 않는다 — 해당 셸은 **기존 mcp_server의 credential 교체만** 담당하기 때문 (binding-dialog spec §4.3.a).

M5에서 선택할 동선은 **옵션 A를 채택**한다:

- **옵션 A (채택)**: "연결 추가" CTA를 누르면 **`add-tool-dialog` MCP 탭이 열린다**. 사용자가 거기서 서버 생성 + credential 설정을 마치면 `/connections` MCP 섹션으로 자동 복귀 (목록 refetch). 진입점만 `/connections`에서 공유될 뿐 실제 UI는 기존 add-tool-dialog MCP 탭 그대로 — 중복 UX 방지.
- **옵션 B (reject)**: 서버 목록에서 먼저 고르고 credential을 설정 — 하지만 "서버도 없는 사용자"의 최초 진입 시 empty state가 두 번 중첩되어 오히려 복잡. 기각.

구현 힌트 (저커버그):
- `AddToolDialog`는 `trigger` prop을 받는 형태 — `/connections` MCP 섹션의 "연결 추가" 버튼을 그대로 `<AddToolDialog trigger={<Button ... />} />`로 감싸 호출.
- 기본 활성 탭을 `mcp`로 고정하는 prop이 없다면 S3에서 `defaultTab='mcp'` prop 추가가 허용된다 (add-tool-dialog 드라이브-바이는 금지지만, 기본값 prop 한 줄 추가는 허용 범위).

---

## 4. ConnectionCard

```
┌─────────────────────────────────────────────────────────┐
│ [🔗] {display_name}                                      │
│                                                         │
│      [provider badge] [status badge] [기본 배지(선택)]    │
│                                                         │
│      🔑 credential: {credential.name ?? "미지정"}        │
│      🛠  {n}개 도구에서 사용 중                           │
│                                    [상세] [⋮ 메뉴]       │
└─────────────────────────────────────────────────────────┘
```

### 4.1 필드

| 요소 | 데이터 소스 | 비고 |
|---|---|---|
| 아이콘 | type별 lucide (`KeyRoundIcon`/`WrenchIcon`/`ServerIcon`) | 시각 신호 |
| 이름 | `connection.display_name` | 줄바꿈 없이 truncate |
| provider badge | `connection.provider_name` | PREBUILT는 한글 라벨 매핑, MCP/CUSTOM은 원문 |
| status badge | `connection.status` | active = `outline`, disabled = `secondary` + muted |
| "기본" 배지 | `connection.is_default === true` | `secondary` 아주 작게 |
| credential 표시 | `connection.credential_id`를 useCredentials로 lookup → `credential.name` | null이면 "미지정" 회색 텍스트 |
| 사용 중 tool 카운트 | `useToolsByConnection(connection.id)` (신규 derived selector) | 0이면 "사용 중 도구 없음" |

### 4.2 상호작용

| 영역 | 액션 |
|---|---|
| 카드 전체 클릭 | 상세 drawer 오픈 (PC), 풀스크린 sheet (모바일) |
| [상세] 버튼 | 동일 |
| [⋮] 메뉴 | [이름 편집] / [status toggle] / [삭제] |
| [status toggle] | `useUpdateConnection({ status: 'active'|'disabled' })` — 즉시 반영, 에러 시 revert |

**삭제 동작**
- 사용 중 tool 카운트 `> 0`이면 `AlertDialog`로 경고 + **삭제 버튼 disabled** (M5 안전 기본값).
- 카운트 `0`이면 경고 후 삭제 허용.
- PREBUILT의 `is_default=true` connection 삭제 시: 같은 provider의 다른 connection이 없으면 경고 "해당 provider에 연결이 없어지면 모든 tool이 기본값 없이 실행됩니다".

#### 4.3 Credential 삭제 semantics (M5 결정)

ADR-008의 N:1 모델 (여러 Connection이 하나의 Credential을 공유 가능 — CUSTOM 재사용 시나리오)을 반영한 정책:

| 동작 | 처리 |
|---|---|
| **Connection 삭제 ≠ Credential 삭제** | Connection row만 제거한다. 참조하던 credential은 **살아남는다**. 다른 connection이 참조하고 있을 수 있고, 아니더라도 사용자의 "키 자체"는 명시적 삭제 없이 지우지 않는다. |
| 마지막 참조 connection 삭제 후 credential | "고아 credential"로 남는다. `/connections` 1급 리스트에는 보이지 않지만(Credential 카드 제거됨), Connection 생성 시 CredentialSelect에 여전히 선택지로 노출된다. |
| Credential을 실제로 삭제하려면 | Connection 상세 drawer의 **"Credential 편집"** (§5.2) 플로우에서 `CredentialFormDialog` 내 삭제 버튼 사용. 이 동선은 credential row가 **다른 connection에도 참조되고 있으면 차단**한다 (`useCredentials` 응답 + 전체 `useConnections` 조회로 client-side 안전장치 — 서버는 `ON DELETE SET NULL`로 FK 구성되어 있지만 UX는 차단). |
| 고아 credential 일괄 정리 | M5 스코프 외. 후속 "Credential 관리" UX가 필요하면 별도 기획. |

**삭제 버튼 레이블**: Drawer의 "연결 삭제"는 "**이 연결만 삭제**" 로 명시 (credential은 보존됨을 사용자에게 알림). i18n 키: `connections.detail.deleteButton` = "이 연결만 삭제 (credential은 유지)".

---

## 5. Connection 상세 (Drawer)

클릭 시 우측 drawer로 열림 (shadcn `Sheet` 권장 — 기존 프로젝트 내 유사 패턴 확인 필요).

### 5.1 구조

```
┌─ Drawer ────────────────────────────────────┐
│ 🔗 {display_name}                    [✕]    │
│ {provider badge} {status badge}             │
├─────────────────────────────────────────────┤
│ 개요                                         │
│  · 타입        PREBUILT / CUSTOM / MCP      │
│  · Provider   naver / custom_api_key / ...  │
│  · 생성일      2026-04-10                   │
│  · 수정일      2026-04-18                   │
│                                             │
│ Credential (bound)                          │
│  · 이름        내 Naver API 키               │
│  · 타입        API Key / OAuth2             │
│  · 필드        client_id, client_secret     │
│  · [credential 변경]   [credential 편집]     │
│                                             │
│ MCP 상세 (type=mcp 일 때만)                  │
│  · URL         https://...                 │
│  · Transport  http                         │
│  · Auth type  bearer                       │
│  · Headers    2개                          │
│  · Env vars   1개                          │
│                                             │
│ 사용 중 도구 ({n})                           │
│  · [ToolName] → /tools?highlight=...       │
│  · ...                                      │
│                                             │
│ 위험 구역                                    │
│  · [상태 전환: active ↔ disabled]            │
│  · [연결 삭제]                              │
└─────────────────────────────────────────────┘
```

### 5.2 상호작용

- **Credential 변경 (다른 credential로 교체)**: `ConnectionBindingDialog(type, providerName, triggerContext='standalone', currentConnectionId=<this>)` 오픈. Connection row의 `credential_id` FK만 바뀐다. 원래 credential row는 그대로.
- **Credential 편집 (같은 credential의 값 회전)**: `CredentialFormDialog(editingCredential=<credential>)` 직접 오픈. credential row 자체의 field 값(secret rotation)을 교체. Connection은 건드리지 않음.
- **Credential 삭제**: CredentialFormDialog 내 "삭제" 버튼. 단 `useConnections()` 전역 조회로 **다른 connection이 같은 credential_id를 참조 중이면 disabled** + tooltip "다른 연결에서 사용 중입니다. 먼저 해당 연결을 해제하세요." 클릭 가능 시 `AlertDialog` 경고 후 `useDeleteCredential`.
- **상태 전환**: `useUpdateConnection({ status })`. 비활성화 시 "이 연결을 사용하는 도구는 실행 시 실패합니다" inline 경고.
- **Connection 삭제**: 4.2 + 4.3 룰 (연결만 삭제, credential 보존).

세 동작을 시각적으로 구분하기 위해 drawer의 "Credential (bound)" 섹션에 세 버튼을 나란히 배치:
```
[credential 변경]  [credential 편집]       (⋮ 메뉴 안 → [credential 삭제])
```
`credential 삭제`는 파괴적 동작이므로 기본 노출에서 한 단계 숨긴다 (팀쿡 디자인 원칙: 위험도 ≠ 접근성).

---

## 6. 빈 상태

### 6.1 페이지 전체 빈 상태 (connection 0개)

```
┌─────────────────────────────────────────────┐
│         🔗                                   │
│   아직 연결이 없습니다                        │
│   도구에서 재사용할 API 키를 등록해 보세요.    │
│                                             │
│   [PREBUILT 연결 추가]                       │
│   [CUSTOM 연결 추가]                         │
│   [MCP 연결 추가]                            │
└─────────────────────────────────────────────┘
```

### 6.2 섹션별 빈 상태

PREBUILT provider별 그룹:
```
┌─ Naver ─────────────────── [+ 연결 추가] ─┐
│ 아직 Naver 연결이 없습니다.                  │
└──────────────────────────────────────────┘
```

CUSTOM / MCP:
```
┌─ CUSTOM 연결 ─────────── [+ 연결 추가] ─┐
│ 아직 CUSTOM 연결이 없습니다.               │
└──────────────────────────────────────────┘
```

---

## 7. 삭제 / 제거 항목 (회귀 방지 체크리스트)

| 제거 대상 | 위치 | 교체 |
|---|---|---|
| 기존 Credential 카드 목록 | `page.tsx:142-162` | 삭제 — Connection Card로 대체 |
| Search / Type Filter | `page.tsx:108-134` | 삭제 또는 전역 필터로 축소 (M5 스코프에선 단순 삭제 권장) |
| `[연결 추가]` 최상단 버튼 (현재 Credential 생성) | `page.tsx:130-133` | 섹션별 CTA로 분산 |
| `PrebuiltConnectionSection` 인라인 구현 | `page.tsx:204-300` | 신규 `PrebuiltConnectionSection` 컴포넌트로 재작성 |
| `AlertDialog` (credential 삭제) | `page.tsx:172-199` | Connection 삭제 AlertDialog로 대체 |
| `CredentialFormDialog` 최상단 호출 | `page.tsx:166-170` | 삭제 — Connection 상세 drawer 내부에서만 사용 |

**유지 대상**
- `useCredentials` / `useCredentialProviders` hook — Connection 상세 drawer 내 credential 이름 표시, credential 편집에서 계속 사용.
- `CredentialFormDialog` 컴포넌트 자체.

---

## 8. 접근성

- [ ] 섹션 헤더는 `<h2>` 시맨틱, provider 그룹 헤더는 `<h3>`.
- [ ] Connection Card는 `role="region"` + `aria-labelledby={idOfName}` — 스크린리더가 카드 단위로 인식.
- [ ] Drawer 오픈 시 focus는 drawer 닫기 버튼으로 이동, 닫으면 트리거 카드로 복귀.
- [ ] [⋮] 메뉴는 shadcn `DropdownMenu` — 키보드 네비 기본 제공.
- [ ] 빈 상태 CTA 버튼도 키보드로 도달 가능하도록 tabindex 확인.
- [ ] status toggle(Switch 또는 Button)은 `aria-checked` / `aria-pressed` 명시.

---

## 9. i18n 키 (신규)

```jsonc
"connections": {
  "pageTitle": "연결 관리",
  "pageDescription": "도구가 재사용할 외부 서비스 연결을 관리합니다.",

  "sections": {
    "prebuilt": {
      "title": "PREBUILT 연결",
      "description": "시스템 도구(Naver, Google 등)에 사용할 API 키를 관리합니다.",
      "addButton": "연결 추가",
      "providerEmpty": "아직 {provider} 연결이 없습니다."
    },
    "custom": {
      "title": "CUSTOM 연결",
      "description": "사용자 정의 도구에서 재사용할 API 키를 관리합니다.",
      "addButton": "연결 추가",
      "empty": "아직 CUSTOM 연결이 없습니다."
    },
    "mcp": {
      "title": "MCP 연결",
      "description": "MCP 서버에 연결해 추가 도구를 가져옵니다.",
      "addButton": "연결 추가",
      "empty": "아직 MCP 연결이 없습니다."
    }
  },

  "card": {
    "credentialUnbound": "credential 미지정",
    "usedByTools": "{count}개 도구에서 사용 중",
    "noUsage": "사용 중 도구 없음",
    "isDefaultBadge": "기본",
    "statusActive": "활성",
    "statusDisabled": "비활성"
  },

  "detail": {
    "sectionOverview": "개요",
    "sectionCredential": "Credential",
    "sectionMcp": "MCP 상세",
    "sectionUsage": "사용 중 도구",
    "sectionDanger": "위험 구역",
    "changeCredential": "credential 변경",
    "editCredential": "credential 편집",
    "toggleToDisabled": "비활성화",
    "toggleToActive": "활성화",
    "disabledWarning": "이 연결을 사용하는 도구는 실행 시 실패합니다.",
    "deleteButton": "이 연결만 삭제",
    "deleteButtonHint": "credential은 유지됩니다",
    "deleteBlockedByUsage": "사용 중인 도구가 있어 삭제할 수 없습니다. 먼저 도구의 연결을 교체하세요.",
    "credentialChange": "credential 변경",
    "credentialEdit": "credential 편집",
    "credentialDelete": "credential 삭제",
    "credentialDeleteBlocked": "다른 연결에서 사용 중입니다. 먼저 해당 연결을 해제하세요."
  },

  "emptyPage": {
    "title": "아직 연결이 없습니다",
    "description": "도구에서 재사용할 API 키를 등록해 보세요."
  },

  "toast": {
    "statusChanged": "연결 상태가 변경되었습니다",
    "statusFailed": "연결 상태 변경에 실패했습니다",
    "deleted": "연결이 삭제되었습니다",
    "deleteFailed": "연결 삭제에 실패했습니다"
  }
}
```

**기존 키 처리**
- `connections.prebuiltSection.*`: 재사용 가능한 카피만 새 네임스페이스로 이관, 나머지는 deprecated (S6 이전에 정리).
- `connections.empty.*`, `connections.deleteConfirm` 등 Credential 중심 키: S4에서 미사용 처리 (M5 스코프에선 삭제하지 않고 방치, M6 cleanup에서 드롭).

---

## 10. 파생 Selector — useToolsByConnection

Connection Card의 "사용 중 tool 카운트"를 위해 신규 유도 selector가 필요하다.

```ts
// frontend/src/lib/hooks/use-connections.ts (신규 유도 export)

export function useToolsByConnection(connectionId: string) {
  const { data: tools } = useTools()  // 기존 훅 재사용
  return useMemo(
    () => tools?.filter((t) => t.connection_id === connectionId) ?? [],
    [tools, connectionId],
  )
}
```

**설계 메모**
- `useTools()`가 이미 전역 캐시에 있으므로 추가 요청 없음.
- `mcp_server`가 여러 tool을 공유하는 경우(MCP): `tool.mcp_server_id → mcp_server.connection_id` 이중 hop이 필요 — S4에서 저커버그가 `useMCPServers()` 재사용 확인 후 구현. 필요하면 `useToolsByConnection`을 type별로 분기.

---

## 11. 수용 기준 (S4 검증용)

- [ ] `/connections` 진입 시 3개 섹션(PREBUILT / CUSTOM / MCP)만 렌더, Credential 카드 노출 0건.
- [ ] 각 섹션 "연결 추가" CTA가 `ConnectionBindingDialog`를 type 고정으로 오픈.
- [ ] 기존 M3 사용자가 만든 PREBUILT connection이 새 UI에 그대로 보임 (Naver default 회귀 없음).
- [ ] 기존 M4 사용자가 `/tools`에서 만든 CUSTOM connection이 새 CUSTOM 섹션에 노출됨.
- [ ] Connection Card 클릭 → drawer 오픈 → credential 메타/사용 tool 목록/status toggle/삭제 동작.
- [ ] 사용 중 tool 있는 connection 삭제 시도 → 차단 + 경고 toast.
- [ ] 빈 상태 2종(전체 / 섹션) 카피 및 CTA 정상 표시.
- [ ] i18n 신규 키 `connections.sections.*`, `connections.card.*`, `connections.detail.*` 추가.
- [ ] Drive-by 금지: 백엔드 파일 변경 0, `lib/api/credentials.ts` 시그니처 변경 0, M5.5/M6 영역 수정 0.

---

## 11b. 구현 시 회귀 주의 (저커버그 S4)

### 11b.1 `tools/page.tsx` `getAuthStatus` (베조스 S1 §7-4 Bezos "?")

본 spec의 범위는 `/connections` 페이지이지만, **Connection 카드의 "사용 중 tool 카운트" 계산을 위해 `useTools()` 전역 캐시를 읽는다**. `tools/page.tsx`가 같은 캐시로 "configured" 배지를 판정한다면, 양쪽 페이지가 서로 다른 필드(예: `tool.credential_id` vs `tool.connection_id`)로 판정하는 상황이 발생할 수 있다. S4 구현 시:

- [ ] `useToolsByConnection(connectionId)` 파생 selector는 **MCP / CUSTOM 각각 필터 조건이 다르다** — CUSTOM은 `tool.connection_id === id`, MCP는 `tool.mcp_server_id`가 해당 서버를 가리키는지 확인 후 `mcp_server.credential_id`와 Connection 매칭. 반드시 type별로 분기.
- [ ] 기존 M4 bridge row(`tool.credential_id != null && tool.connection_id != null`)도 카운트에 올바로 포함되는지 확인 — credential_id 기반이 아닌 **connection_id 기반**으로 매칭.
- [ ] tools/page.tsx `getAuthStatus`는 S3 저커버그가 **CustomAuthDialog 교체 시 함께 확인**. 본 spec(§11b)은 같은 이슈의 `/connections` 쪽 파생 영향만 다룸.

### 11b.2 Credential 고아 상태 검증

- [ ] Connection 삭제 후 `/connections` 어디에도 해당 credential이 보이지 않지만, 다른 Connection 생성 시 CredentialSelect에는 노출되는지 수동 E2E 확인 (베조스 S5).
- [ ] Credential 삭제가 "다른 connection이 참조 중이면 disabled" 되는지 확인 — 전역 `useConnections()` refetch 타이밍 주의.

---

## 12. 오픈 이슈 / M5 스코프 외

- **Connection 편집 (display_name / is_default 승격)**: 최소 inline edit만 제공 (ConnectionBindingDialog의 credential 변경 흐름으로 대체). 독립 "Connection 편집 폼"은 M5에선 만들지 않음 (비용 대비 가치 낮음, 필요 시 후속).
- **사용량 통계(usage analytics)**: 현재 카운트만. 최근 호출/토큰 소비 등은 `/usage` 도메인 범위.
- **Search / Filter 복원**: 섹션 분리로 개념 명료해졌으므로 M5에선 검색 없이 출시. 다중 connection을 가진 사용자가 늘면 재추가.
- **MCP Headers/env_vars 상세 편집 UX**: ConnectionBindingDialog 스펙(§4.3)에 최소 구현 명시. 개선은 후속.
