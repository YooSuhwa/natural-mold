# Marketplace UI Design Spec (M8a Slice G)

> 작성일: 2026-05-18
> 작성자: 팀쿡 (TTH Designer / UX)
> 관련 문서: PRD v0.2 §10·§11, Spec v0.1 §10, ADR-017, ADR-010 (디자인 토큰 + DialogShell), Module Contracts §3
> 목적: Phase 1 Skill marketplace의 정보 구조, 상태 표시, 카드/테이블 컬럼, 사용자 흐름, 컴포넌트 매핑, API 인터페이스를 명문화하여 저커버그(M8b 구현자)를 언블록한다.
> 산출물 범위: text-only design spec — pixel-perfect wireframe은 의도적으로 제외 (저커버그가 shadcn/DialogShell/DataTable 위에 구현하면 시각적 일관성은 자동 확보).

---

## 0. Design Posture

### 0.1 디자인 원칙

1. **재사용 우선 (Musk Step 2 — 삭제)**: 신규 컴포넌트는 마켓플레이스 도메인 특유의 요소(`OriginBadge`, `PublicationBadge`, `InstallWizard`, `PublishWizard`, `MarketplaceCard`, `MarketplaceFilterBar`, `CredentialSummaryChip`, `UpdateAvailableBanner`)에만 한정. 나머지(`Card`, `DataTable`, `Dialog`/`DialogShell`, `Badge`, `Tabs`, `Select`, `Combobox`, `Form`, `AlertDialog`, `Sheet`)는 shadcn/ui + ADR-010 토큰 그대로 재사용한다.

2. **인지 부하 최소화 (Musk Step 3 — 단순화)**: 카드 1개에 표시하는 1차 정보는 7개 슬롯을 넘기지 않는다(name, description, owner, resource type, visibility, latest version, credential summary). 그 이상은 detail 화면으로 미룬다.

3. **출처/게시 상태의 시각적 일관성**: `OriginBadge`와 `PublicationBadge`는 marketplace catalog뿐 아니라 `/skills`, `/mcp-servers`, agent dashboard 어디서도 동일 컴포넌트로 렌더한다 — 사용자가 "이게 내 것인지, 가져온 것인지, 공유한 것인지"를 한 호흡에 판별할 수 있게.

4. **상태 표현은 색 단독으로 의존하지 않는다 (WCAG 2.1 AA)**: 모든 상태 chip은 텍스트 라벨 + 아이콘 + 색을 함께 사용. `is_listed=False`인 public item은 빨강이 아니라 `text-muted-foreground + 자물쇠 아이콘 + "Unlisted"` 라벨로 표현.

5. **풀스택 일관성**: 백엔드 API surface(Spec §10.1~§10.7, progress.txt L60–101)와 1:1로 매핑되는 hook/api 함수를 만들고, 그 위에 페이지/컴포넌트를 짓는다. UI에서 만든 임시 상태(local cache, optimistic update 등)는 backend contract와 어긋나면 안 된다.

6. **에러는 코드별로 분기**: Spec §10.7의 12개 에러 코드는 각각 다른 UX 분기를 가진다. generic toast 한 줄로 묶지 않는다(§6 Error Matrix 참조).

### 0.2 토큰/패턴 재사용 매핑 (ADR-010)

| 용도 | 토큰/클래스 | 적용처 |
|------|------------|--------|
| 카드 강조 hover/active border | `ring-1 ring-border/60` + `hover:ring-primary-strong/30` | `MarketplaceCard` |
| 강조 텍스트 (link/primary CTA) | `text-primary-strong` | "Install" / "View details" 등 |
| 강조 배경 (selected tab indicator) | `bg-primary-strong` (after pseudo) | `Tabs` |
| Subtle badge (kind, locale) | `bg-primary/15 text-primary-strong` | OriginBadge `built_in_k_skill` |
| 정보성 chip (`hosted_proxy`) | `bg-status-info/10 text-status-info` | CredentialSummaryChip |
| 경고 chip (`needs setup`, `unlisted`) | `bg-status-warn/10 text-status-warn` | InstallationStatusChip |
| 위험 chip (`disabled`, `secret detected`) | `bg-destructive/10 text-destructive` | PublicationBadge `disabled` |
| 성공 chip (`active`, `published_public_listed`) | `bg-status-success/10 text-status-success` | InstallationStatusChip |
| accent chip (`shared_with_me`, `restricted`) | `bg-status-accent/10 text-status-accent` | OriginBadge `shared_with_me` |
| Dialog 컨테이너 | `DialogShell` + `DIALOG_SIZE.lg`(install) / `DIALOG_SIZE.xl`(publish) | InstallWizard / PublishWizard |
| Wizard side step list | `DialogShell.Sidebar` (260px) | PublishWizard 5-step |
| Table | `DataTable` (`columnId`, `FilterDef`) | `/marketplace`, `/skills` 갱신 |
| 카드 grid | `grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-4` | `/marketplace` 기본 view |
| 빈 상태 | `EmptyState` | catalog 검색 결과 없음 등 |

### 0.3 접근성 기준

- 모든 인터랙티브 요소는 `focus-visible:ring-2 ring-ring` 통과 (ADR-010 D).
- Wizard step indicator는 `aria-current="step"`.
- Origin/Publication badge는 텍스트 라벨이 sr-only가 아니라 visible (스크린리더 + 시각 모두 같은 정보).
- AlertDialog (overwrite/install_new_copy/keep_current 선택)는 destructive 액션을 명확히 표시 (`variant="destructive"` + 두 번째 확인 필요).
- 키보드 네비게이션: 카드 → Enter로 detail, 카드 내 primary CTA → Tab으로 진입 가능.
- 색 단독으로 의미를 전달하지 않음. 모든 상태 chip은 텍스트 + 아이콘 동반.

---

## 1. Page Inventory

| 경로 | 역할 | 진입 | 접근권 |
|------|------|------|--------|
| `/marketplace` | 카탈로그 (탭: All / Agents / MCP / Skills / Installed) | Sidebar `Marketplace` 클릭 | 로그인 사용자 모두 |
| `/marketplace/[item-id]` | item detail (latest version, versions list, credential requirements, install CTA, ACL 관리(owner)) | catalog 카드 클릭, 또는 share link | 가시성에 따른 `can_view_item` 통과자 |
| `/marketplace/installed` | 내 설치본 관리 (skill/mcp/agent 합본 — Phase 1은 skill만 의미) | catalog 탭 `Installed` 또는 사이드바 직접 링크 | 본인만 |
| `/marketplace/publish` | publish wizard 진입 hub (보통 `/skills/{id}` detail에서 `Publish to Marketplace` 버튼을 통해 진입) | `/skills/{id}` `Publish` CTA | skill owner |
| `/marketplace/admin/moderation` | super_user moderation (미승인 public 항목 큐) | Sidebar `Admin > Moderation` (super_user만 visible) | `is_super_user=True` |
| `/skills` (기존 페이지 업데이트) | 내 skill 관리 — Origin/Publication 컬럼 + Marketplace source 컬럼 + `Publish` CTA 추가 | Sidebar `Skills` | 본인 |
| `/mcp-servers` (Phase 2 예약) | Origin/Publication 컬럼만 추가 (install/publish는 Phase 2) | Sidebar `MCP Servers` | 본인 |

**경로 결정 사유**:
- `/marketplace/[item-id]`를 root path로 둠 (resource_type 세그먼트를 path에 넣지 않음) — 같은 item_id면 resource type이 자동 결정되므로 URL이 짧고 share link copy가 단순.
- `/marketplace/publish`는 standalone wizard 페이지가 아니라 catalog 헤더의 `Publish` 버튼 → `/skills` 선택 → `PublishWizard` dialog 형식. 사용자는 항상 자기 owned skill을 출발점으로 publish하기 때문.
- `/marketplace/installed`는 catalog 탭 `Installed`로 충분하지만, dashboard에서 "내가 설치한 것만 빨리 보고 싶다"는 워크플로를 위해 별도 path도 유지.

### 1.1 사이드바 진입점

기존 사이드바 항목(`Dashboard / Agents / Skills / MCP Servers / Models / Tools / Credentials / Usage`) 사이에 추가:

```
Dashboard
Agents
Skills
MCP Servers
─────────────
Marketplace          [new]
  Catalog
  Installed
  Moderation         [super_user only]
─────────────
Models
Tools
Credentials
Usage
```

- `Marketplace`는 그룹 헤더. 하위 항목 클릭 시 `/marketplace`, `/marketplace/installed`, `/marketplace/admin/moderation`로 라우팅.
- 그룹은 기본 펼침. localStorage에 collapse 상태 저장.
- super_user가 아닌 사용자에게 `Moderation`은 숨김 (백엔드 403도 가드 — UI 가시성은 보조).

---

## 2. 카드 / 테이블 / 상세 정보 구조

### 2.1 `MarketplaceCard` (catalog grid)

카드 컴포넌트의 슬롯 (위→아래, 좌→우):

```
┌─ Card ────────────────────────────────────────────┐
│ ① [Icon]  ② Name                ⑩ [Install CTA]   │
│           ③ owner · resource    ⑪ Card menu (⋮)   │
│                                                   │
│ ④ Description (2-line clamp)                      │
│                                                   │
│ ⑤ [Origin]  ⑥ [Publication]  ⑦ [Credential chip]  │
│ ⑧ [Support level chip] ⑨ [latest version]         │
└───────────────────────────────────────────────────┘
```

| # | 슬롯 | 데이터 소스 | 컴포넌트 |
|---|------|------------|---------|
| ① | Icon (24px) | `item.icon_url` 또는 resource_type 기본 아이콘 (`Sparkles`/`Server`/`Bot`) | `Icon` |
| ② | Name | `item.name` | `text-sm font-semibold` |
| ③ | Owner + type | `is_system ? "System" : item.owner.email`, `resource_type` | `text-xs text-muted-foreground` |
| ④ | Description | `item.description` (2-line clamp via `line-clamp-2`) | `text-sm text-muted-foreground` |
| ⑤ | Origin | `item.origin_summary.kind` (catalog에서는 owner 시점이 아닌 viewer 시점에서 derive — `built_in_k_skill`, `community`, `shared_with_me`, `created_by_me`) | `OriginBadge` |
| ⑥ | Publication | `item.publication_summary.state` (다른 사용자의 item은 항상 `published_*` 또는 `disabled`. owner 카드에서는 `draft`/`not_published`도 가능) | `PublicationBadge` |
| ⑦ | Credential summary | `item.credential_summary.status` | `CredentialSummaryChip` |
| ⑧ | Support level | `item.execution_profile.support_level` | `SupportLevelChip` |
| ⑨ | Latest version | `item.latest_version.version_label`, `created_at` (relative time) | `text-xs text-muted-foreground` |
| ⑩ | Primary CTA | `item.installation` 기반 매핑 (§2.4) | `Button` (size `sm`) |
| ⑪ | Card menu | `View details` / `Copy link` / (owner) `Manage` / (super_user) `Toggle listed` / `Disable` | `DropdownMenu` |

**N+1 회피**: 카드에 표시되는 모든 데이터는 단일 `GET /api/marketplace/items` 응답에서 옴 (`origin_summary`, `publication_summary`, `credential_summary`, `installation`, `execution_profile`, `latest_version` 모두 embed). 페이지가 카드별로 추가 호출하지 않는다.

### 2.2 카드 CTA 상태 매핑 (PRD §11.1)

`primary_cta`는 `(installation.installed, installation.status, installation.update_available, installation.dirty, item.is_disabled, execution_profile.support_level)`의 함수로 결정.

| 우선순위 | 조건 | Primary CTA 라벨 | 액션 | variant |
|---------|------|-----------------|------|---------|
| 1 | `item.status='disabled'` | `Disabled` | disabled button (tooltip: "이 항목은 비활성화되었습니다") | `outline`, disabled |
| 2 | `support_level in ('manual_only', 'browser_or_local')` AND NOT installed | `View details` | item detail 이동 | `outline` |
| 3 | NOT installed | `Install` | InstallWizard 오픈 | `default` |
| 4 | installed AND `status='needs_setup'` | `Set up` | InstallWizard step 2(credential)로 직진 | `default` (warn variant) |
| 5 | installed AND `update_available` AND `dirty` | `Review update` | UpdateDialog 오픈 (3-strategy 선택) | `outline` |
| 6 | installed AND `update_available` AND NOT `dirty` | `Update` | UpdateDialog 오픈 (default `overwrite` highlight) | `default` |
| 7 | installed AND active | `Open` | `/skills/{installed_resource_id}` 이동 | `outline` |
| 8 | installed AND `status='disabled'` | `Disabled` | tooltip | `outline`, disabled |

`Card menu` 추가 항목:
- 모두: `View details`, `Copy link`
- Owner: `Manage`, `New version`, `Disable`
- Super_user: `Toggle listed`, `Disable`
- Installed: `Uninstall`

### 2.3 카드 변형 (탭별)

| 탭 | 카드 grid 출력 |
|----|---------------|
| `All` | resource_type 무관 전체 (Phase 1은 사실상 skill만 의미). 카드 ② 옆에 type 배지 |
| `Agents` (Phase 2+) | 빈 상태 + "Coming in Phase 3" 텍스트 + 카드 grid 비활성 |
| `MCP` (Phase 2+) | 빈 상태 + "Coming in Phase 2" 텍스트 + 카드 grid 비활성 |
| `Skills` | Phase 1 메인 view. 기본 정렬: `is_listed_first, then created_at DESC` |
| `Installed` | viewer 본인의 installations만. 같은 카드 컴포넌트 사용하되 `installation` 슬롯이 항상 채워져 있어 CTA가 `Open`/`Update`/`Set up`/`Review update`/`Disabled` 중 하나 |

### 2.4 `MarketplaceFilterBar` (카탈로그 상단)

```
[Search input            ] [Resource type ▾] [Source ▾] [Visibility ▾] [Credential ▾] [Install state ▾] [Support ▾]
                                                                                       ⤷ "Reset filters" link
```

| 필터 | 컴포넌트 | API param | 옵션 |
|------|---------|----------|------|
| 검색 | `SearchInput` | `q` | free text. debounce 250ms |
| Resource type | `Select` | `resource_type` | All / Skill / MCP(Phase 2) / Agent(Phase 3) |
| Source | `Select` | `source_kind` | All / `user` / `k-skill` / `import` / `system_seed` |
| Visibility | `Select` | `visibility` | All / public / restricted / private / unlisted / system. Phase 1에서 catalog는 보통 public+restricted+system만 의미. owner는 자기 private도 봄 |
| Credential | `Select` | `credential_status` | All / `none` / `required` / `optional` / `hosted_proxy` / `manual_login` |
| Install state | `Select` | `install_state` | All / `not_installed` / `installed` / `needs_setup` / `update_available` / `dirty` |
| Support | `Select` | `support_level` | All / `ready_python` / `proxy_http` / `node_package` / `browser_or_local` / `manual_only` |
| Locale | `Select` (optional, advanced) | `locale` | All / ko-KR / en-US |

**Listed toggle (super_user only)**: 별도 우측에 `Show unlisted` toggle. `is_listed=false` 쿼리 추가. 일반 사용자에게는 보이지 않음.

기본 정렬 `?sort=` (Phase 1은 server-side sort 미지원 → frontend 기본 `is_listed_first, then created_at DESC`).

### 2.5 카탈로그 헤더 액션

```
┌ /marketplace ────────────────────────────────────────┐
│ Marketplace                              [Publish ▾] │
│ Discover and install shared skills…                  │
│                                                      │
│ [Tabs: All  Skills  Agents  MCP  Installed]          │
└──────────────────────────────────────────────────────┘
```

`Publish ▾` 메뉴:
- `Publish a skill` → `/skills` 페이지로 이동 + 안내 toast ("Choose a skill to share")
- `Request k-skill sync` (super_user only) → `/marketplace/admin/moderation` (k-skill status 영역)

### 2.6 `/marketplace/[item-id]` Detail 화면

```
┌ Breadcrumb: Marketplace › Skills › {name} ────────────────────────┐
│ ┌─ Hero ──────────────────────────────────────────────────────────┐│
│ │ [Icon] {name}                                  [Primary CTA]    ││
│ │        {owner · resource_type · locale}        [Card menu ⋮]    ││
│ │ [Origin] [Publication] [Credential] [Support] [latest version]  ││
│ │ Description (full)                                              ││
│ └─────────────────────────────────────────────────────────────────┘│
│                                                                   │
│ ┌─ Left column ─────────────┐  ┌─ Right column ─────────────────┐ │
│ │ ## Credential requirements │  │ ## Versions                    │ │
│ │ (CredentialRequirementList)│  │ (VersionsTable)                │ │
│ │                            │  │                                │ │
│ │ ## Execution profile       │  │ ## Source                      │ │
│ │ - runner: python           │  │ - source_kind: k-skill         │ │
│ │ - requires_network         │  │ - upstream: github.com/...     │ │
│ │ - notes: …                 │  │ - commit: 80303f5              │ │
│ │                            │  │                                │ │
│ │ ## Tags / Categories       │  │ ## Shared with                 │ │
│ │ (badges)                   │  │ (restricted only — ACL chips)  │ │
│ └────────────────────────────┘  └────────────────────────────────┘ │
│                                                                   │
│ ## Owner / Moderation actions (conditional)                       │
│   - Owner: Edit metadata, Manage ACL, Publish new version, Disable │
│   - Super_user: Toggle listed, Disable                            │
└───────────────────────────────────────────────────────────────────┘
```

**Owner actions row** (only when current_user == item.owner_user_id):
- `Edit metadata` — name/description/tags/icon `PATCH /api/marketplace/items/{id}`
- `Manage shared users` — restricted ACL `POST/DELETE acl`
- `Publish new version` — PublishWizard short flow (release_notes만)
- `Disable` — AlertDialog 확인 후 `POST /disable`

**Super_user actions row**:
- `Toggle listing` — `POST /admin/items/{id}/listed`
- `Disable` — `POST /admin/items/{id}/disable`

### 2.7 `/skills` 페이지 업데이트 (DataTable 컬럼)

| 컬럼 | 타입 | 데이터 |
|------|------|--------|
| Name | text | `skill.name` (font-medium) |
| Kind | Badge | `text` / `package` |
| Origin | `OriginBadge` | `skill.origin_summary.kind` |
| Marketplace | composite | `skill.publication_summary.state` (PublicationBadge) + `skill.source_marketplace_item_id`가 있으면 source name link |
| Credential | `CredentialSummaryChip` | `skill.credential_summary.status` (status만 — required count는 detail에서) |
| Used by | number | `skill.used_by_count` (agent count) |
| Updated | relative date | `skill.updated_at` |

**행 액션** (DataTable row menu):
- Open detail (기존)
- `Publish to Marketplace` (publication_state ∈ `not_published` 일 때만)
- `Update from marketplace` (`installation.update_available=true`)
- `Sync now` (super_user only — k-skill 항목)

### 2.8 `/mcp-servers` 페이지 업데이트 (Phase 2 예약 컬럼)

| 컬럼 | 타입 | 데이터 |
|------|------|--------|
| Name | text | `mcp.name` |
| Transport | Badge | `stdio` / `sse` / `streamable_http` |
| Origin | `OriginBadge` | `mcp.origin_summary.kind` (Phase 1은 항상 `created_by_me`) |
| Marketplace | `PublicationBadge` | `mcp.publication_summary.state` (Phase 1은 모두 `not_published`) |
| Credential | `CredentialSummaryChip` | reserved |
| Health | StatusChip | M26 health_status |
| Updated | relative date | `mcp.updated_at` |

Phase 1은 origin/publication 컬럼이 표시되되 install/publish CTA는 비활성. tooltip: "Coming in Phase 2".

---

## 3. 핵심 사용자 흐름

### 3.1 흐름 1 — PRD §10.1 built-in k-skill 설치 (no-credential)

**진입 트리거**: `/marketplace` Skills 탭 → `korean-spell-check` 카드 → `Install` 클릭.

**단계**:

1. `InstallWizard` 오픈 (DialogShell size `lg`, height `auto`).
   - Header: icon, title "Install korean-spell-check", description "한국어 맞춤법 검사 skill을 내 계정에 설치합니다."
   - Right action: `<OriginBadge kind="built_in_k_skill" />` + `<CredentialSummaryChip status="none" />`
2. **Step 1: Review**.
   - 화면:
     - Resource type, latest version, source(`k-skill@80303f5`), execution profile (`ready_python · requires_network`)
     - "이 skill은 credential이 필요하지 않습니다." 안내 (status=`none`)
     - Optional `name_override` input (placeholder: `korean-spell-check`)
   - Footer: `Cancel` | `Install`
3. **Step 2: 없음** (credential 없으므로 즉시 install).
4. **Install 액션**: `POST /api/marketplace/items/{item_id}/install` body `{install_mode: "reuse_or_update", install_missing_credentials: "needs_setup", credential_bindings: {}}`.
5. **성공**: Dialog 닫힘 + toast "Installed. Open in Skills" (액션: `/skills/{installed_resource_id}`로 이동). 카드 CTA가 `Open`으로 바뀜 (optimistic + invalidate `useMarketplaceItem` query).

**실패 경로**:

| 에러 코드 | 처리 |
|----------|------|
| `marketplace_item_not_found` (404) | Dialog 닫힘 + redirect to `/marketplace` + toast "이 항목을 찾을 수 없거나 접근 권한이 없습니다." (enumeration oracle에 의해 권한 없음도 같은 메시지) |
| `marketplace_item_disabled` (409) | Dialog 내부에 ErrorState + "이 항목은 비활성화되었습니다. 운영자에게 문의하세요." + `Close` 버튼 |
| `marketplace_invalid_package` (400) | ErrorState + 디버그 정보 없이 generic "패키지 검증 실패" + 운영자 문의 안내 |
| network 오류 | toast + retry 버튼 |

### 3.2 흐름 2 — PRD §10.2 credential required skill 설치 (`srt-booking`)

**진입 트리거**: `/marketplace` → `srt-booking` 카드 → `Install` 클릭.

**단계**:

1. `InstallWizard` 오픈 (size `lg`, height `fixed`). Sidebar 사용 (4-step indicator).
   - 4-step indicator: `① Review · ② Credentials · ③ Confirm · ④ Done`
2. **Step 1: Review**.
   - Hero summary (version, source, support level)
   - `<CredentialSummaryChip status="required" requiredCount={1} missingRequiredCount={1} />`
   - 표시: "이 skill은 SRT 계정 자격증명을 필요로 합니다."
   - Footer: `Cancel` | `Next`
3. **Step 2: Credentials**.
   - 한 줄당 한 `CredentialRequirementRow` (compound: `requirement_key`, definition icon, label, description, fields chip, scope).
   - 각 row에 Combobox: `useCredentialsByDefinition(definition_key)`로 user의 호환 credential 후보 + `+ Create new credential` 항목.
   - Combobox 비어 있으면 inline empty state: "호환 credential이 없습니다. 새로 만들기"
   - `+ Create new credential` 선택 시 right pane에 inline `<DynamicFieldsForm>` (기존 `/credentials` 컴포넌트 재사용) — 저장 시 `POST /api/credentials/` 후 자동 선택.
   - Optional skip toggle: "지금은 건너뛰고 나중에 연결 (needs_setup으로 설치)" → `install_missing_credentials="needs_setup"` 전송
   - Footer: `Back` | `Next` (required 모두 binding되거나 skip toggle ON이면 활성)
4. **Step 3: Confirm**.
   - 요약 카드: skill name, version, name_override, credential bindings 표 (`requirement_key → credential name`)
   - "Install"을 누르면 다음 동작이 일어납니다:
     - 사용자 owned skill row 생성 (data/skills/<id>/)
     - skill_credential_bindings row 생성
     - origin: `built_in_k_skill`
   - Footer: `Back` | `Install`
5. **Step 4: Done**.
   - Success state + `<InstallationStatusChip status={installation.status} />`
   - 다음 액션: `Open in Skills` 또는 `Attach to agent` (link to `/agents`).

**실패 경로**:

| 에러 | 단계 | 처리 |
|------|------|------|
| `marketplace_credential_required` (409, install_missing_credentials="reject"일 때) | Step 3 | Step 2로 복귀 + 누락 requirement row를 빨강 outline + 안내 "이 자격증명은 필수입니다" |
| `marketplace_credential_mismatch` (400) | Step 3 | 해당 requirement row inline error "선택한 credential의 종류가 일치하지 않습니다 (선택 가능한 종류: srt_account)" + Combobox 재선택 유도 |
| credential 생성 중 422 (definition validation) | Step 2 inline form | DynamicFieldsForm의 표준 inline error 표시 |
| `marketplace_item_disabled` (409) | Step 1~3 | 전체 wizard ErrorState + Close |
| network/500 | toast + Step 유지 + Retry |

**accessibility**: Step indicator는 `role="list"` + 각 step `aria-current="step"`. Wizard 닫기 시 dirty 데이터 있으면 confirm AlertDialog ("입력한 내용이 사라집니다").

### 3.3 흐름 3 — PRD §10.3 사용자 자기 skill 공유 (5-step publish wizard)

**진입 트리거**: `/skills/{skill_id}` detail dialog → 우상단 `Publish to Marketplace` 버튼. 또는 `/skills` 페이지 row menu의 `Publish to Marketplace`.

**단계**:

1. `PublishWizard` 오픈 (DialogShell size `xl`, height `tall`, Split layout).
   - Sidebar (5-step indicator):
     - `① Review files · ② Metadata · ③ Credentials · ④ Visibility · ⑤ Confirm`
2. **Step 1: Review files**.
   - Skill package tree (`<SkillPackageTree>` 재사용) — 어떤 파일이 packaging되는지 보여준다.
   - Top alert: "Marketplace에 게시되기 전 secret 검사가 실행됩니다."
   - 만약 frontend가 pre-scan할 수 있다면(클라이언트에서는 어렵다 — 서버 실 응답에 의존), 이 단계에서는 안내만.
   - Footer: `Cancel` | `Next`
3. **Step 2: Metadata**.
   - Form fields: `name`, `description`, `tags` (`Combobox` multi), `categories` (`Combobox` multi), `icon_url`(optional), `release_notes`
   - validation: name required, description recommended (≥30자 안내 — block 아님)
   - Footer: `Back` | `Next`
4. **Step 3: Credentials**.
   - PRD/Spec §11.5 — 현재 skill의 `credential_requirements`를 표시.
   - 사용자가 manually requirement를 추가/편집 가능 (env_map은 advanced toggle 안쪽).
   - 각 requirement row: `key`, `definition_key` (Select from registered definitions), `label`, `required` toggle, `scope` (`user`/`system_dependency`/`manual`), `injection` (env 고정)
   - `+ Add credential requirement` 버튼
   - **Beginner mode** (default ON): "이 skill은 credential이 필요하지 않습니다" 한 줄로 비워두고 Next 가능.
   - Footer: `Back` | `Next`
5. **Step 4: Visibility**.
   - `Visibility` Select: `private` / `restricted` / `public` / `unlisted`
   - `private` 선택: 추가 input 없음
   - `restricted` 선택: `acl_user_ids` Combobox (search by email) — 최소 1명 필요(`marketplace_acl_required`)
   - `public` 선택: 안내 alert "공개 publish 후에도 카탈로그 검색 노출은 운영자 승인이 필요합니다. (Unlisted from search until approved)"
   - `unlisted` 선택: 안내 alert "직접 링크를 가진 사용자만 접근할 수 있습니다."
   - Footer: `Back` | `Next`
6. **Step 5: Confirm**.
   - 요약: 모든 입력값
   - "다음을 수행합니다:" 액션 리스트 (item 생성/업데이트, 신규 version 생성, ACL row 생성 (해당 시), publication link 생성)
   - Footer: `Back` | `Publish`
7. **Publish 액션**: `POST /api/marketplace/items/from-skill/{skill_id}` body `PublishSkillIn`.
8. **성공**: Dialog 닫힘 + redirect to `/marketplace/[item_id]` + toast "Published. Visibility: {state}".

**실패 경로**:

| 에러 | 단계 | 처리 |
|------|------|------|
| `marketplace_secret_detected` (400) | Step 5 (서버 응답) | Step 1로 복귀 + 빨강 alert + secret 검출 파일 목록(`detail.findings[].path` + pattern). "secret을 제거한 뒤 다시 시도하세요." |
| `marketplace_acl_required` (400) | Step 4 client-validation에서 미리 차단. 서버 응답 시에는 Step 4로 복귀 + acl combobox 강조 |
| `marketplace_invalid_visibility` (400) | Step 5 (재publish에서 visibility 전이 불가 시) | Step 4 복귀 + 가능한 visibility 안내 |
| `marketplace_manage_forbidden` (403) | Step 5 (재publish 권한 회수된 케이스) | toast + dialog 닫힘 + skills 페이지로 |
| `marketplace_invalid_package` (400) | Step 5 | Step 1 복귀 + "패키지 검증 실패: SKILL.md 누락" 같은 구체 사유 |

**dirty 입력 보호**: ESC/Close 시 confirm AlertDialog.

### 3.4 흐름 4 — PRD §10.4 restricted ACL 공유

**진입 트리거**: PublishWizard Step 4에서 `restricted` 선택 → Step 5 publish. 또는 기존 item detail의 `Manage shared users`.

**Manage shared users 흐름**:

1. `/marketplace/[item-id]` (owner view) → `Manage shared users` 버튼.
2. `SharedUsersDialog` 오픈 (DialogShell size `md`).
3. 현재 ACL chips 리스트 (`<UserChip email="…" onRemove={…} />`).
4. `+ Add user` Combobox — email search (`GET /api/users?search=…`).
5. Add 시 `POST /api/marketplace/items/{item_id}/acl` `{user_ids: [uuid], permission: "install"}`.
6. Remove 시 `DELETE /api/marketplace/items/{item_id}/acl/{user_id}`.
7. **실패: `marketplace_acl_required`** — 마지막 user를 remove하려 하면 inline error "restricted item은 최소 1명의 공유 대상이 필요합니다. visibility를 먼저 private으로 변경하세요."

**대상 사용자 측 경험**:

- catalog에서 item 카드가 보임 (`OriginBadge=shared_with_me`, `PublicationBadge=published_restricted`).
- Install CTA 표시. 정상 install 흐름 진입.
- 제거되면: catalog에서 카드 사라짐. 이미 install된 copy는 `/skills`에 그대로 (Spec §7.5 D7).

### 3.5 흐름 5 — PRD §10.5 update available 적용 (overwrite vs new copy)

**진입 트리거**: `/marketplace` 또는 `/skills`에서 `installation.update_available=true`인 항목 → `Update` (또는 dirty인 경우 `Review update`) CTA.

**단계 (clean update — dirty=false)**:

1. `UpdateDialog` 오픈 (DialogShell size `md`, height `auto`).
   - Header: title "Update korean-spell-check", description "v1 → v2 업데이트가 있습니다."
2. **Body**:
   - 현재 version vs latest version 메타 비교 (release_notes 표시)
   - 3-strategy 카드 (radio group):
     - **Overwrite** (`overwrite`, default) — "현재 설치본을 최신 버전으로 교체합니다. 개인 수정 사항은 사라집니다." (dirty=false면 손실 없음 안내)
     - **Install as new copy** (`install_new_copy`) — "현재 설치본을 그대로 두고 새 사본을 추가로 설치합니다."
     - **Keep current** (`keep_current`) — "업데이트하지 않고 현재 상태를 유지합니다." (단순 dismiss + remember)
3. **Confirm**: `POST /api/marketplace/installations/{installation_id}/update` body `{strategy}`.
4. **성공**: toast "Updated to v2" + invalidate `useSkill(installed_skill_id)` + `useMarketplaceItem(item_id)`.

**Dirty update 경로 (dirty=true)**:

1. CTA는 `Review update`.
2. UpdateDialog 진입 시 상단에 `<UpdateAvailableBanner variant="dirty" />`: "이 설치본을 직접 수정한 기록이 있습니다. 덮어쓰면 변경사항이 사라집니다."
3. `Overwrite` radio 옆에 `<Badge variant="destructive">Destructive</Badge>` 표시.
4. Confirm 시 `Overwrite` 선택은 AlertDialog 2차 확인 ("내 변경 사항을 모두 잃어버립니다. 계속하시겠습니까?").

**실패**:

| 에러 | 처리 |
|------|------|
| `marketplace_dirty_installation` (409, strategy 미지정으로 호출했을 때 — 백엔드 가드) | UpdateDialog로 redirect (strategy 선택 강제) |
| `marketplace_version_not_found` (404) | toast "버전 정보를 찾을 수 없습니다. 새로고침 후 다시 시도하세요." + invalidate |

### 3.6 흐름 6 (보너스) — PRD §10.7 super_user moderation 승인

**진입 트리거**: Sidebar `Admin > Moderation` (super_user only) → `/marketplace/admin/moderation`.

**페이지 구조**:

```
┌ Page header: Moderation                          ┐
│ Public items pending listing approval            │
│                                                  │
│ [Tabs: Pending(N) · Disabled · k-skill status]   │
│                                                  │
│ DataTable                                        │
│  Name · Owner · Type · Created · Credential · ⋯  │
│  Row CTA: Review · Approve listing · Disable     │
└──────────────────────────────────────────────────┘
```

**Tab 1: Pending** — `GET /api/marketplace/admin/moderation` (is_listed=False AND visibility=public AND status=published).

**행 액션**:
- `Review` → `/marketplace/[item-id]` (super_user 뷰. owner action row + super_user action row 둘 다 보임)
- `Approve listing` → AlertDialog confirm → `POST /api/marketplace/admin/items/{item_id}/listed` `{is_listed: true}` → row가 Pending 탭에서 사라짐 + toast "Listed"
- `Disable` → AlertDialog confirm → `POST /api/marketplace/admin/items/{item_id}/disable` → toast "Disabled"

**Tab 2: Disabled** — `?status=disabled` 필터. row 액션 `Re-enable`.

**Tab 3: k-skill status** — `GET /api/marketplace/admin/k-skill/sync` (status 조회. 실제 sync는 CLI). 화면:
- 마지막 sync 시각, upstream ref, item count
- 안내 코드 블록: `uv run python -m app.scripts.sync_k_skill --ref main`
- "Sync는 CLI에서만 실행할 수 있습니다." 안내 (Spec §D12)

**실패**:
- `marketplace_manage_forbidden` (403) — 권한이 회수된 경우. redirect to `/marketplace` + toast.

---

## 4. State Machine 및 컴포넌트 매핑

### 4.1 Visibility × Actor × Status UI affordance 매트릭스

Spec §12.1 매트릭스를 UI affordance로 매핑. 표기: `L`=List(catalog 노출), `D`=Detail 페이지 진입, `I`=Install CTA, `M`=Manage 액션, `★`=Listed 토글.

| Status | Visibility | Owner | ACL user | Unrelated | Super_user |
|--------|-----------|-------|---------|-----------|------------|
| draft | private | L·D·M | — | — | D·M |
| draft | restricted | L·D·M | D·I | — | D·M |
| published | private | L·D·I·M | — | — | D·M |
| published | restricted | L·D·I·M | L·D·I | — | L·D·M |
| published | public+listed | L·D·I·M | L·D·I | L·D·I | L·D·M·★ |
| published | public+unlisted | L·D·I·M | L·D·I | D·I (link only) | L·D·M·★ |
| published | unlisted | L·D·I·M | — | D·I (link only) | L·D·M |
| published | system | L·D·I | L·D·I | L·D·I | L·D·M·★ |
| deprecated | * | L·D·M | L·D | L·D (badge) | L·D·M |
| disabled | * | D·M | — | — | D·M |

**렌더 규칙**:

- `L` 없으면 `/marketplace` 카탈로그 카드 grid에 표시 안 됨. detail 직접 URL은 `D`로 별도 결정.
- `I` 없으면 카드/detail CTA가 `View details`(`outline`)로 fallback.
- `M` 없으면 owner action row 자체를 렌더하지 않음.
- `★` 없으면 super_user action row의 `Toggle listing` 버튼 비활성/숨김.
- `disabled` × `unrelated`는 detail 진입 자체가 404 (enumeration oracle).

### 4.2 Installation Status × UI 표시

| `installation.status` | 카드 우상단 chip | 카드 CTA | detail banner |
|----------------------|----------------|---------|--------------|
| (없음 — not installed) | (없음) | `Install` | (none) |
| `active` | `Installed` (`status-success`) | `Open` 또는 `Update` | (없음) |
| `needs_setup` | `Needs setup` (`status-warn`) + 자물쇠 아이콘 | `Set up` | yellow banner "이 설치본은 자격증명 연결이 필요합니다. [Open setup]" |
| `disabled` | `Disabled` (`destructive`) | `Disabled` (disabled) | red banner "이 설치본은 비활성화되었습니다." |
| `uninstalled` | (카드에서 안 보임) | `Install` | (uninstalled는 catalog에서 not_installed처럼 보임) |

`update_available`은 chip 추가: `Update available` (`status-info`).
`is_dirty`는 chip 추가: `Modified` (`status-accent`) — installed copy를 직접 수정함을 표시.

### 4.3 컴포넌트 매핑 (신규 / 재사용)

**신규 컴포넌트** (`frontend/src/components/marketplace/`):

| 컴포넌트 | 파일 | 책임 |
|---------|------|------|
| `MarketplaceCard` | `marketplace-card.tsx` | catalog/installed 탭에서 사용하는 단일 item 카드 |
| `MarketplaceFilterBar` | `marketplace-filter-bar.tsx` | 검색 + 필터 셀렉트 그룹 |
| `OriginBadge` | `origin-badge.tsx` | 6개 origin kind 렌더 |
| `PublicationBadge` | `publication-badge.tsx` | 8개 publication state 렌더 |
| `CredentialSummaryChip` | `credential-summary-chip.tsx` | none/optional/required/hosted_proxy/manual_login 5 status |
| `SupportLevelChip` | `support-level-chip.tsx` | 6 support level (`ready_python` 등) |
| `InstallationStatusChip` | `installation-status-chip.tsx` | active/needs_setup/disabled/uninstalled + update_available + dirty 조합 |
| `InstallWizard` | `install-wizard.tsx` | 4-step (Review/Credentials/Confirm/Done) |
| `UpdateDialog` | `update-dialog.tsx` | 3-strategy radio (overwrite/new_copy/keep_current) |
| `PublishWizard` | `publish-wizard.tsx` | 5-step (Files/Metadata/Credentials/Visibility/Confirm) |
| `SharedUsersDialog` | `shared-users-dialog.tsx` | ACL 추가/제거 |
| `CredentialRequirementRow` | `credential-requirement-row.tsx` | InstallWizard Step 2 / Publish Step 3 공통 row |
| `UpdateAvailableBanner` | `update-available-banner.tsx` | detail 상단/카드 상단 배너 (variants: `default`, `dirty`, `disabled_source`) |
| `VersionsTable` | `versions-table.tsx` | item detail 우측 column versions list |
| `CredentialRequirementList` | `credential-requirement-list.tsx` | item detail 좌측 column requirement 리스트 |
| `MarketplaceSourceLink` | `marketplace-source-link.tsx` | `/skills` row + skill detail에서 source item link (small text + chevron) |

**재사용 컴포넌트** (변경 없이):

- `Card`, `CardHeader`, `CardTitle`, `CardDescription` (ui/card)
- `Dialog`, `DialogShell`, `Sheet` (shared)
- `Button`, `Badge`, `Input`, `Textarea`, `Select`, `Combobox`(custom — `CommandList` 패턴)
- `DataTable`, `FilterDef` (ui/data-table)
- `Tabs`, `LineTabs`
- `AlertDialog`
- `EmptyState`, `ErrorState`, `StatusChip`, `PageHeader`, `SearchInput`
- `DynamicFieldsForm` (credential 신규 생성 inline)
- `SkillPackageTree` (publish wizard step 1)
- `Icon`, `Skeleton`, `Tooltip`

### 4.4 Badge 시각 명세

#### OriginBadge (PRD §6 — Resource Origin)

| kind | label | 아이콘 (lucide) | 색 (token) |
|------|-------|---------------|-----------|
| `created_by_me` | Created by me | `Pencil` | `bg-muted text-foreground` |
| `imported_by_me` | Imported by me | `Download` | `bg-muted text-foreground` |
| `built_in_k_skill` | Built-in · k-skill | `Sparkles` | `bg-primary/15 text-primary-strong` |
| `shared_with_me` | Shared by {name} | `Users` | `bg-status-accent/10 text-status-accent` |
| `community` | Community | `Globe` | `bg-status-info/10 text-status-info` |
| `system_seed` | System | `Cog` | `bg-muted text-foreground` |

#### PublicationBadge (PRD §6 — Publication State)

| state | label | 아이콘 | 색 |
|-------|-------|--------|----|
| `not_published` | Not published | `EyeOff` | `bg-muted text-muted-foreground` |
| `draft` | Draft | `FilePen` | `bg-muted text-foreground` |
| `published_private` | Private | `Lock` | `bg-muted text-foreground` |
| `published_restricted` | Restricted | `UserCheck` | `bg-status-accent/10 text-status-accent` |
| `published_public_listed` | Listed | `CheckCircle2` | `bg-status-success/10 text-status-success` |
| `published_public_unlisted` | Unlisted (pending) | `Hourglass` | `bg-status-warn/10 text-status-warn` |
| `published_unlisted` | Unlisted (link) | `Link` | `bg-status-info/10 text-status-info` |
| `disabled` | Disabled | `Ban` | `bg-destructive/10 text-destructive` |

#### CredentialSummaryChip

| status | label | 아이콘 | 색 |
|--------|-------|--------|----|
| `none` | No credential | `CircleDashed` | `bg-muted text-muted-foreground` |
| `optional` | Optional credential | `Plus` | `bg-muted text-foreground` |
| `required` | Credential required | `Key` | `bg-status-warn/10 text-status-warn` |
| `hosted_proxy` | Hosted proxy | `Cloud` | `bg-status-info/10 text-status-info` |
| `manual_login` | Manual login | `LogIn` | `bg-status-accent/10 text-status-accent` |

`missing_required_count > 0`이면 chip에 빨강 dot indicator (visually `after:bg-destructive`).

#### SupportLevelChip

| level | label | 색 |
|-------|-------|----|
| `ready_python` | Python ready | `bg-status-success/10 text-status-success` |
| `proxy_http` | Proxy required | `bg-status-info/10 text-status-info` |
| `node_package` | Node required | `bg-muted text-muted-foreground` |
| `browser_or_local` | Browser/local | `bg-status-warn/10 text-status-warn` |
| `manual_only` | Manual only | `bg-status-warn/10 text-status-warn` |
| `disabled` | Unsupported | `bg-destructive/10 text-destructive` |

---

## 5. API 호출 매핑

각 페이지/wizard가 호출하는 backend endpoint를 정리. `lib/api/marketplace.ts`와 `lib/hooks/useMarketplace*.ts`로 구현.

### 5.1 페이지별 API 매핑

| 페이지/컴포넌트 | hook | endpoint | invalidation |
|---------------|------|----------|--------------|
| `/marketplace` catalog | `useMarketplaceItems(filters)` | `GET /api/marketplace/items?…` | install/uninstall/update 후 |
| `/marketplace` Installed 탭 | `useMarketplaceItems({installed: true})` | 동일 (필터에 `installed=true`) | install/uninstall/update 후 |
| `/marketplace/[item-id]` | `useMarketplaceItem(item_id)`, `useMarketplaceVersions(item_id)` | `GET /api/marketplace/items/{item_id}` + `GET /api/marketplace/items/{item_id}/versions` | publish/update_metadata/acl/disable 후 |
| `/marketplace/admin/moderation` | `useModerationQueue()`, `useKSkillStatus()` | `GET /api/marketplace/admin/moderation`, `GET /api/marketplace/admin/k-skill/sync` | listed 토글/disable 후 |
| InstallWizard | `useInstallItem()` | `POST /api/marketplace/items/{item_id}/install` | item + skills + installations |
| UpdateDialog | `useUpdateInstallation()` | `POST /api/marketplace/installations/{installation_id}/update` | item + skill + installation |
| Uninstall | `useUninstall()` | `DELETE /api/marketplace/installations/{installation_id}?delete_resource=…` | item + skills |
| PublishWizard | `usePublishSkill()` | `POST /api/marketplace/items/from-skill/{skill_id}` | items + skills(publication_summary) |
| New version | `usePublishNewVersion()` | `POST /api/marketplace/items/{item_id}/versions/from-skill/{skill_id}` | item + versions |
| Metadata edit | `useUpdateItemMetadata()` | `PATCH /api/marketplace/items/{item_id}` | item |
| ACL add | `useAddItemAcl()` | `POST /api/marketplace/items/{item_id}/acl` | item |
| ACL remove | `useRemoveItemAcl()` | `DELETE /api/marketplace/items/{item_id}/acl/{user_id}` | item |
| Disable (owner) | `useDisableItem()` | `POST /api/marketplace/items/{item_id}/disable` | items + item |
| Admin listed toggle | `useAdminToggleListed()` | `POST /api/marketplace/admin/items/{item_id}/listed` | items + moderation queue |
| Admin disable | `useAdminDisableItem()` | `POST /api/marketplace/admin/items/{item_id}/disable` | items + moderation queue |
| Publication status (내 보드) | `usePublicationStatus()` | `GET /api/marketplace/publication-status` | publish 후 |
| Skill credential reqs | `useSkillCredentialRequirements(skill_id)` | `GET /api/skills/{skill_id}/credential-requirements` | install 후 |
| Skill credential bindings | `useSkillCredentialBindings(skill_id)` | `GET /api/skills/{skill_id}/credential-bindings` | bind/unbind 후 |
| Bind credential | `useSetSkillCredentialBinding()` | `PUT /api/skills/{skill_id}/credential-bindings/{key}` | bindings + skill(needs_setup→active) |
| Unbind | `useUnsetSkillCredentialBinding()` | `DELETE /api/skills/{skill_id}/credential-bindings/{key}` | bindings + skill |

### 5.2 응답 shape 예시

`MarketplaceItemOut` 응답 (catalog 카드/detail에서 사용):

```json
{
  "id": "uuid",
  "resource_type": "skill",
  "name": "korean-spell-check",
  "slug": "korean-spell-check",
  "description": "한국어 맞춤법 검사…",
  "visibility": "system",
  "status": "published",
  "is_system": true,
  "is_listed": true,
  "latest_version": {
    "id": "uuid",
    "version_label": "0.1.0",
    "version_number": 1,
    "content_hash": "sha256…",
    "source_commit": "80303f5",
    "created_at": "2026-05-15T…"
  },
  "credential_summary": {
    "status": "none",
    "required_count": 0,
    "optional_count": 0,
    "missing_required_count": 0
  },
  "execution_profile": {
    "support_level": "ready_python",
    "runners": ["python"],
    "requires_network": true
  },
  "origin_summary": {
    "kind": "built_in_k_skill",
    "label": "Built-in · k-skill",
    "source_name": "k-skill",
    "marketplace_item_id": "uuid",
    "marketplace_version_id": "uuid"
  },
  "publication_summary": {
    "state": "published_public_listed",
    "item_id": "uuid",
    "visibility": "system",
    "status": "published",
    "is_listed": true,
    "latest_version_id": "uuid",
    "version_number": 1,
    "shared_user_count": 0
  },
  "installation": {
    "installed": false,
    "installation_id": null,
    "installed_resource_id": null,
    "status": null,
    "update_available": false,
    "dirty": false
  }
}
```

`CredentialRequirementOut` (Step 2 Credentials):

```json
{
  "key": "srt_account",
  "definition_key": "srt_account",
  "required": true,
  "label": "SRT account",
  "description": "SRT 로그인 자격증명",
  "fields": ["username", "password"],
  "injection": "env",
  "scope": "user"
}
```

`InstallationSummary` (카드 우상단 chip):

```json
{
  "installed": true,
  "installation_id": "uuid",
  "installed_resource_id": "uuid",
  "status": "needs_setup",
  "update_available": true,
  "dirty": false
}
```

### 5.3 에러 코드 → UI 처리 매트릭스

표기 — Layer: `T`=Toast, `B`=Banner, `IF`=Inline Field error, `M`=Modal/ErrorState, `R`=Redirect.

| code | HTTP | 발생 위치 | Layer | UX 문구 (한국어) | 다음 액션 |
|------|------|----------|-------|----------------|----------|
| `marketplace_item_not_found` | 404 | catalog/detail/install | M+R | "이 항목을 찾을 수 없거나 접근 권한이 없습니다." | `/marketplace`로 redirect |
| `marketplace_version_not_found` | 404 | install/update | T | "버전 정보를 찾을 수 없습니다. 새로고침 후 다시 시도하세요." | invalidate item query |
| `marketplace_install_forbidden` | 404 | install | M+R | (item_not_found과 동일 메시지 — enumeration 일관) | redirect |
| `marketplace_manage_forbidden` | 403 | manage 액션 | T+R | "이 작업을 수행할 권한이 없습니다." | `/marketplace` redirect (manage 뷰 이탈) |
| `marketplace_item_disabled` | 409 | install/update | B+M | "이 항목은 비활성화되었습니다." | wizard 닫기 / Close 버튼 |
| `marketplace_invalid_visibility` | 400 | publish (visibility 전이) | IF | "이 visibility 변경은 허용되지 않습니다." | Step 4로 복귀, 가능한 옵션 안내 |
| `marketplace_acl_required` | 400 | publish/acl-remove | IF | "restricted 게시는 최소 1명의 공유 대상이 필요합니다." | Step 4(publish) / inline error(SharedUsersDialog) |
| `marketplace_invalid_package` | 400 | publish | M | "패키지 검증 실패: SKILL.md를 확인하세요." | Step 1로 복귀 |
| `marketplace_secret_detected` | 400 | publish/upload | M+IF | "Secret이 포함된 파일이 감지되었습니다. 제거 후 다시 시도하세요." (+ findings 목록) | Step 1로 복귀, 파일 목록 강조 |
| `marketplace_credential_required` | 409 | install/runtime | B+IF | "이 skill을 실행하려면 자격증명을 연결해야 합니다." | InstallWizard Step 2 직진 + 필수 row 강조 |
| `marketplace_credential_mismatch` | 422 | install/binding PUT | IF | "선택한 credential의 종류가 일치하지 않습니다 (필요: {definition_key})." | Combobox 재선택 |
| `marketplace_dirty_installation` | 409 | update | M | "이 설치본은 수정된 상태입니다. 업데이트 방식을 선택하세요." | UpdateDialog 강제 진입 |

**기타 표준**:
- 401 (auth 만료) — global interceptor가 `/auth/login`으로 redirect (기존 패턴 재사용).
- 5xx — toast "일시적 오류가 발생했습니다. 잠시 후 다시 시도하세요." + retry 버튼 (가능한 곳).

---

## 6. Edge Case 및 빈 상태

### 6.1 빈 상태 (EmptyState 컴포넌트)

| 상황 | 페이지 | 아이콘 | 헤드라인 | 보조 텍스트 | CTA |
|------|--------|--------|---------|------------|-----|
| 카탈로그 자체가 비어 있음 (system seed 전, k-skill sync 전) | `/marketplace` | `Sparkles` | "아직 공유된 항목이 없습니다" | "내 skill을 공유하거나 운영자가 k-skill을 동기화하면 여기에 표시됩니다." | (owner) `Publish a skill` 또는 (super_user) k-skill 안내 |
| 검색 결과 없음 | `/marketplace` | `Search` | "결과가 없습니다" | "필터 또는 검색어를 조정해보세요." | `Reset filters` |
| Installed 탭 비어 있음 | `/marketplace/installed` | `Package` | "아직 설치한 항목이 없습니다" | "카탈로그에서 마음에 드는 항목을 설치해보세요." | `Browse catalog` → `/marketplace` |
| Pending(moderation) 큐 비어 있음 | `/marketplace/admin/moderation` | `CheckCircle2` | "처리 대기 항목이 없습니다" | "공개 게시된 항목이 모두 승인되었습니다." | — |
| Versions 없음 (draft only) | item detail | `FilePen` | "아직 publish된 버전이 없습니다" | (owner) "PublishWizard에서 첫 버전을 만들어보세요." | (owner) `Publish first version` |
| 사용자 owned skill이 없는데 publish 진입 | `/marketplace` Publish 메뉴 | `Plus` | "공유할 skill이 없습니다" | "먼저 Skills에서 skill을 만들거나 가져오세요." | `/skills`로 이동 |
| 호환 credential 없음 (InstallWizard Step 2) | InstallWizard | `KeyRound` | "호환되는 자격증명이 없습니다" | "이 skill은 {definition_key} 타입의 credential이 필요합니다." | `Create new credential` (inline DynamicFieldsForm) |

### 6.2 Edge Cases

| 케이스 | UX |
|-------|----|
| **인증 만료 (401)** | 글로벌 interceptor가 `/auth/login` redirect (기존 ADR-016 패턴) + 원래 위치 `?next=` 보존 |
| **CSRF 토큰 만료** | 백엔드가 403 + `code: csrf_invalid` 반환 → 자동 재발급 후 한 번 재시도, 실패 시 toast "세션이 만료되었습니다." |
| **네트워크 오류 (offline)** | TanStack Query offline 표시 + 카드 grid는 캐시 데이터 + 상단 banner "오프라인 상태입니다." |
| **부분 sync 결과 (admin k-skill status)** | last sync report에 `failed` count 있으면 yellow chip "Partial — N failed" + 상세는 CLI 로그 안내 |
| **`is_dirty` 상태에서 update 시도** | 카드 CTA가 `Review update`로 바뀜 + UpdateDialog에서 destructive 옵션 명확화 + 2차 confirm (3.5 참조) |
| **publish 도중 dialog 닫기** | dirty form일 때 confirm AlertDialog ("입력한 내용이 사라집니다") |
| **catalog 응답 큰 페이지 (1000+ items)** | Phase 1은 페이지네이션 server-side 없으면 frontend 가상화 (`@tanstack/react-virtual`)로 — 초기 30개만 grid 렌더 + scroll-load. Backend 페이지네이션 추가는 Phase 2 |
| **카드 이미지 깨짐** | `icon_url` 로드 실패 시 resource_type 기본 아이콘으로 fallback (`onError`) |
| **disabled item 직접 URL 접근** | super_user는 detail 진입 + disabled banner 표시. 일반 사용자는 404 → `/marketplace` redirect + toast |
| **restricted item 접근권 회수 직후 catalog** | 카드가 사라짐(`useMarketplaceItems` refetch). 이미 install된 copy는 `/skills`에 그대로 — Origin이 `shared_with_me`로 유지되지만 `installation.update_available`은 false로 고정 (마켓플레이스에 접근 못 하므로) |
| **multi-tab race condition** | Mutation 후 `invalidateQueries` 외에 SSE/websocket으로는 sync 안 함 (Phase 1 범위). 다른 탭은 사용자가 새로고침해야 최신 |
| **k-skill sync 진행 중 카탈로그 조회** | 백엔드는 트랜잭션 단위로만 보이므로 부분 결과 안 보임. 카탈로그는 sync 전 상태 또는 sync 후 상태 둘 중 하나만. UI 별도 처리 불필요 |
| **OriginBadge `shared_with_me`의 source_user_id 사용자가 deactivated** | label fallback "Shared by (former user)" — 백엔드 `source_name` null일 때 처리 |

### 6.3 모바일/태블릿 적응형

- 카드 grid `grid-cols-1 md:grid-cols-2 xl:grid-cols-3`.
- FilterBar는 모바일에서 `Sheet`(우측 슬라이드)로 collapse — `Filter` 버튼 1개로 통합.
- InstallWizard/PublishWizard sidebar는 모바일에서 상단 step indicator로 전환 (sidebar 숨김, `LineTabs` 스타일 step bar).
- DataTable은 모바일에서 horizontal scroll. 필수 컬럼(Name, Origin, Marketplace)만 sticky.

---

## 7. 구현 우선순위 (저커버그용 가이드)

저커버그가 M8b에서 구현할 때 추천 슬라이스:

1. **Slice U1 — 기반 컴포넌트**: `OriginBadge`, `PublicationBadge`, `CredentialSummaryChip`, `SupportLevelChip`, `InstallationStatusChip`. 단독 storybook/preview로 모든 variant 렌더. 다른 슬라이스의 의존성.
2. **Slice U2 — `/skills` 페이지 업데이트**: 컬럼 추가(Origin/Marketplace/Credential), row menu에 `Publish to Marketplace`. 백엔드는 이미 origin/publication summary embed. 가장 작은 변경으로 즉시 가치.
3. **Slice U3 — `/marketplace` catalog read-only**: `MarketplaceCard`, `MarketplaceFilterBar`, 4-tab. `Install` CTA는 placeholder ("Coming soon" toast).
4. **Slice U4 — InstallWizard**: 4-step + credential inline create. credential mismatch 분기 포함.
5. **Slice U5 — `/marketplace/[item-id]` detail**: versions table, credential requirements list, owner action row.
6. **Slice U6 — UpdateDialog + Uninstall**.
7. **Slice U7 — PublishWizard**: 5-step + secret_detected 분기.
8. **Slice U8 — SharedUsersDialog + ACL CRUD**.
9. **Slice U9 — `/marketplace/admin/moderation`** (super_user UI guard).
10. **Slice U10 — `/mcp-servers` 컬럼 업데이트** (Phase 2 표시이지만 origin/publication 컬럼은 노출).

각 슬라이스는 백엔드 변경 없이 완결 (백엔드는 M2~M7에서 이미 완료).

---

## 8. 검증 체크리스트 (M8b 진입 전)

- [ ] 신규 컴포넌트 16개가 `frontend/src/components/marketplace/` 폴더에 1:1 매핑
- [ ] `OriginBadge`/`PublicationBadge`가 `/skills` 페이지에서 동일 컴포넌트 재사용
- [ ] InstallWizard 4-step, PublishWizard 5-step 모두 `DialogShell` 사용 (`DIALOG_SIZE`/`DIALOG_HEIGHT` 토큰)
- [ ] 에러 코드 12개 각각 다른 UX 분기 (§5.3 매트릭스)
- [ ] 모든 색은 ADR-010 토큰 사용 — raw emerald/violet/amber/sky/red 직접 사용 금지
- [ ] 카드 CTA 8가지 분기 (§2.2)가 모두 표현됨
- [ ] OriginBadge 6 kind × PublicationBadge 8 state × CredentialSummaryChip 5 status × SupportLevelChip 6 level × InstallationStatusChip 4 status가 모두 시각적으로 구분 가능
- [ ] WCAG 2.1 AA: 모든 chip이 색 + 라벨 + 아이콘 3중 인코딩
- [ ] 모바일에서 FilterBar/Wizard sidebar 적응형 처리
- [ ] Enumeration oracle: 비인가 detail/install이 404로 통일 (UI도 동일 toast 메시지)

---

## 9. 메모

- **풀스택 일관성**: backend `MarketplaceItemOut` shape이 변경되면 본 spec과 hook 인터페이스를 함께 갱신한다. Pydantic v2 → TS type은 schema generator(zod/openapi-typescript 등)를 쓰는 게 이상적이지만 Phase 1은 수동 type definition (`lib/types/marketplace.ts`)으로 시작 — 단순함 우선.
- **Phase 2 확장 포인트**: MCP marketplace는 `MarketplaceCard`/`InstallWizard`가 resource_type='mcp' 분기만 추가하면 동일 페이지에서 작동. Agent도 마찬가지. 본 spec의 컴포넌트 시그니처는 resource_type을 받아 동작하도록 설계됨.
- **본 문서와 ADR-017이 충돌하면 ADR-017이 우선. Spec과 충돌하면 Spec이 우선** (소스 원본).
