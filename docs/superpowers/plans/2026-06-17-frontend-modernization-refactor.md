# Frontend Modernization Refactor Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Refactor the Moldy frontend around modern Next.js 16 / React 19 / shadcn composition practices, with a primary focus on folder boundaries, common UI grammar, route-level client/server boundaries, and repeatable preflight checks.

**Architecture:** Keep Moldy's current Next.js App Router stack, but make route files thinner and move reusable domain work into focused shared/feature modules. Prefer Server Component route wrappers with small Client Component islands, use shadcn primitives through Moldy-owned wrappers, and add lint/preflight checks so future frontend work follows the new structure. Do not rewrite the product UI wholesale; migrate high-duplication resource/settings surfaces first, then split high-risk chat and wizard modules after baseline tests are stable.

**Tech Stack:** Next.js 16.2.2, React 19.2.4, TypeScript strict mode, Tailwind CSS v4, shadcn/ui, Radix/Base UI, TanStack Query v5, TanStack Table, Jotai, next-intl, Playwright, Vitest, Moldy design-system scripts.

---

## 1. Why This Refactor Exists

The frontend has grown feature-by-feature. Many screens work, but the implementation now mixes three patterns:

1. Good shared primitives that already exist but are not used everywhere.
2. Route-specific components living globally under `src/components`.
3. Large Client Components that combine data loading, layout, interaction state, rendering, and side effects.

The requested refactor should fix the structure first, then enforce the structure with docs and checks.

Primary user-facing goals:

- Make new pages faster to build because lists, settings panels, filters, tabs, cards, dialogs, loading, empty, and error states have one obvious grammar.
- Reduce duplicated menu/sidebar/page shell implementations.
- Stop route-only components from accumulating in global component folders.
- Keep performance from getting worse during commonization by adding preflight and route/client-boundary checks.
- Leave future developers clear rules in `frontend/AGENTS.md`; `frontend/CLAUDE.md` already delegates to `@AGENTS.md`.

## 2. External Research Summary

Use these sources as the design baseline:

- Next.js Project Structure: `https://nextjs.org/docs/app/getting-started/project-structure`
  - Next.js is intentionally unopinionated, but App Router supports route groups, private folders, and colocation.
- Next.js Server and Client Components: `https://nextjs.org/docs/app/getting-started/server-and-client-components`
  - Pages/layouts are Server Components by default; use Client Components for state, effects, browser APIs, and event handlers.
- Next.js Lazy Loading: `https://nextjs.org/docs/app/guides/lazy-loading`
  - Use `next/dynamic` or `React.lazy` for heavy Client Components.
- Next.js `optimizePackageImports`: `https://nextjs.org/docs/app/api-reference/config/next-config-js/optimizePackageImports`
  - Large barrel-style package imports can hurt build/dev performance. Next already optimizes packages such as `lucide-react` by default.
- TanStack Query Advanced SSR: `https://tanstack.com/query/v5/docs/framework/react/guides/advanced-ssr`
  - In App Router, Server Components can act like loaders; hydrate/stream client queries only where needed.
- shadcn/ui docs: `https://ui.shadcn.com/docs`
  - Treat shadcn as owned code and composable primitives, not a black-box component library.
- shadcn Sidebar: `https://ui.shadcn.com/docs/components/sidebar`
  - Sidebar is a composition foundation. The app should provide its own config and nav primitives.
- shadcn Data Table: `https://ui.shadcn.com/docs/components/data-table`
  - Do not force every table into one generic component. Extract when the same table behavior is reused.
- React `forwardRef`: `https://react.dev/reference/react/forwardRef`
  - React 19 makes `forwardRef` less necessary; migrate gradually when touching affected components.
- GitHub references:
  - `https://github.com/Kiranism/next-shadcn-dashboard-starter`
  - `https://github.com/arhamkhnz/next-colocation-template`

## 3. Current Source Baseline

Commands used during discovery:

```bash
find frontend/src -type f \( -name '*.tsx' -o -name '*.ts' \) | wc -l
find frontend/src/app -name page.tsx | wc -l
rg "^'use client'|^\"use client\"" frontend/src -n
find frontend/src/app -name page.tsx -print0 | xargs -0 wc -l | sort -nr | head -30
find frontend/src/components frontend/src/lib -type f \( -name '*.tsx' -o -name '*.ts' \) -print0 | xargs -0 wc -l | sort -nr | head -40
```

Observed baseline:

- `frontend/src` has 632 TypeScript/TSX files.
- 310 files start with `'use client'`.
- `frontend/src/app` has 40 `page.tsx` files.
- 28 pages are currently Client Components.
- `src/components` has roughly 342 files.
- `src/lib` has roughly 204 files.
- Largest frontend files:
  - `frontend/src/lib/chat/use-chat-runtime.ts` - 1356 lines
  - `frontend/src/components/chat/assistant-thread.tsx` - 1092 lines
  - `frontend/src/components/mcp/mcp-server-wizard.tsx` - 1047 lines
  - `frontend/src/components/agent/visual-settings/dialogs/schedule-dialog.tsx` - 802 lines
  - `frontend/src/components/ui/sidebar.tsx` - 774 lines
  - `frontend/src/lib/types/index.ts` - 739 lines
  - `frontend/src/components/agent/visual-settings/visual-settings-flow.tsx` - 661 lines
  - `frontend/src/app/settings/memory/page.tsx` - 657 lines

Build baseline attempt:

```bash
cd frontend && pnpm build
```

Current result in this worktree:

- Fails before Next build because `frontend/node_modules` is missing.
- `scripts/copy-rhwp-wasm.mjs` cannot copy `node_modules/@rhwp/core/rhwp_bg.wasm`.
- The repo pins Node 22 in `.node-version`, but this shell reported Node 24/25 during discovery.

This means the first implementation task must establish environment preflight before bundle/performance measurements are trusted.

## 4. Existing Assets To Preserve

Do not replace these blindly. Reuse or tighten them:

- `frontend/src/components/shared/page-shell.tsx`
  - Existing page header/error wrapper.
- `frontend/src/components/shared/resource-layout.tsx`
  - Existing `ResourcePage`, `ResourcePanel`, `ResourceGrid`, `ResourceListCard`, `CountedLineTabs`, `ResourceToolbar`, `ResourceSummaryStrip`.
- `frontend/src/components/shared/dialog-shell.tsx`
  - Existing Moldy dialog shell and size/height token integration.
- `frontend/src/app/settings/_components/settings-shell.tsx`
  - Existing settings page shell.
- `frontend/src/components/ui/data-table.tsx`
  - Generic TanStack Table wrapper currently used mainly by settings/models.
- `frontend/scripts/check-design-system.mjs`
  - Existing design-system guard.
- `frontend/scripts/check-static-i18n.mjs`
  - Existing i18n static text guard.
- `frontend/AGENTS.md`
  - Existing rules for Next.js 16, Tailwind `cn()`, React 19 effect patterns, DialogShell, design-system guard, i18n, and Resource Card grammar.
- `frontend/CLAUDE.md`
  - Contains `@AGENTS.md`; update `AGENTS.md`, not both.

## 5. Target Folder Architecture

Use a hybrid App Router + feature/module structure.

```text
frontend/src/
├── app/
│   ├── <route>/
│   │   ├── page.tsx                 # Server wrapper unless truly impossible
│   │   ├── loading.tsx              # Route skeleton
│   │   ├── error.tsx                # Client error boundary where needed
│   │   ├── _components/             # Route-only UI
│   │   ├── _hooks/                  # Route-only hooks
│   │   └── _lib/                    # Route-only transforms/config
│   └── settings/
│       └── _components/             # Settings-wide shell/nav/panels
├── components/
│   ├── ui/                          # shadcn/base primitives only
│   ├── shared/                      # Cross-domain Moldy primitives
│   └── layout/                      # App shell/sidebar/header primitives
├── features/
│   ├── resources/                   # Reusable resource list grammar
│   ├── schedules/                   # Schedule dialog/list/card shared by agent/settings
│   ├── models/                      # Model selector/test shared by settings/agent
│   ├── credentials/                 # Credential picker/bindings shared by domains
│   └── marketplace/                 # Multi-route marketplace workflows
└── lib/
    ├── api/                         # HTTP clients
    ├── hooks/                       # Shared data hooks only while migrating
    ├── query-keys/                  # Query key factories if split out of hooks
    ├── types/                       # Backend schema-aligned domain types
    └── stores/                      # Jotai atoms
```

Rules:

- A component used by exactly one route belongs in that route's `_components`.
- A component used by multiple routes in one domain belongs in `src/features/<domain>`.
- A component used across unrelated domains belongs in `src/components/shared`.
- `src/components/ui` stays close to shadcn primitives and should not import app/domain hooks.
- Route `page.tsx` should orchestrate data boundary and shell only. Interactive surface goes into a named Client Component.
- Avoid new barrel exports unless there is a measured ergonomics win. Prefer direct imports.

## 6. Migration Map From Current Source

Initial route-only candidates:

| Current path | Target path | Rationale |
| --- | --- | --- |
| `src/components/mcp/mcp-server-wizard.tsx` | `src/app/mcp-servers/_components/mcp-server-wizard.tsx` | Used by MCP servers page; route-specific wizard. |
| `src/components/mcp/mcp-server-detail-dialog.tsx` | `src/app/mcp-servers/_components/mcp-server-detail-dialog.tsx` | Route-specific detail dialog. |
| `src/components/mcp/mcp-import-dialog.tsx` | `src/app/mcp-servers/_components/mcp-import-dialog.tsx` | Route-specific import flow. |
| `src/components/mcp/mcp-tool-table.tsx` | `src/app/mcp-servers/_components/mcp-tool-table.tsx` | MCP-specific table, not generic DataTable. |
| `src/components/tool/tool-catalog.tsx` | `src/app/tools/_components/tool-catalog.tsx` | Tools page-specific catalog/list. |
| `src/components/tool/tool-create-dialog.tsx` | `src/app/tools/_components/tool-create-dialog.tsx` | Tools page-specific creation flow. |
| `src/components/tool/tool-detail-dialog.tsx` | `src/app/tools/_components/tool-detail-dialog.tsx` | Tools page-specific detail. |
| `src/components/skill/skill-card.tsx` | `src/app/skills/_components/skill-card.tsx` | Skills list specific. |
| `src/components/skill/skill-page-dialogs.tsx` | `src/app/skills/_components/skill-page-dialogs.tsx` | Skills page coordinator. |
| `src/components/marketplace/marketplace-filter-bar.tsx` | `src/app/marketplace/_components/marketplace-filter-bar.tsx` | Marketplace list route-specific until reused. |
| `src/components/agent/agent-card.tsx` | `src/app/_components/agent-card.tsx` or `src/features/agents/components/agent-card.tsx` | Dashboard list card. Use feature path only if reused by other agent lists. |
| `src/components/auth/LoginForm.tsx` | `src/app/(auth)/_components/login-form.tsx` | Auth route-specific. |
| `src/components/auth/RegisterForm.tsx` | `src/app/(auth)/_components/register-form.tsx` | Auth route-specific. |

Keep shared for now:

| Current path | Keep / future target | Rationale |
| --- | --- | --- |
| `src/components/auth/UserMenu.tsx` | Keep in `components/auth` or move later to `components/layout` | Used by shell. |
| `src/components/auth/UserAvatar.tsx` | Keep in `components/auth` or move later to `components/shared` | Cross-shell/avatar utility. |
| `src/components/auth/AuthGuard.tsx` | Keep until auth boundary is redesigned | Cross-route behavior. |
| `src/components/model/model-select.tsx` | Move to `features/models/components/model-select.tsx` | Shared by agent settings/model workflows. |
| `src/components/model/model-connection-test.tsx` | Move to `features/models/components/model-connection-test.tsx` | Shared test UI candidate. |
| `src/components/credential/credential-picker.tsx` | Move to `features/credentials/components/credential-picker.tsx` | Shared by model/tool/MCP/skill flows. |
| `src/components/shared/dynamic-fields-form.tsx` | Keep shared | Cross-domain dynamic credential/tool fields. |

High-risk files to split after low-risk structure is stable:

- `src/lib/chat/use-chat-runtime.ts`
- `src/components/chat/assistant-thread.tsx`
- `src/components/agent/visual-settings/visual-settings-flow.tsx`
- `src/components/agent/visual-settings/dialogs/schedule-dialog.tsx`
- `src/components/mcp/mcp-server-wizard.tsx`

## 7. Common UI Grammar To Implement

Create or formalize these shared pieces:

| Component | Path | Purpose |
| --- | --- | --- |
| `SettingsSectionCard` | `src/components/shared/settings-section-card.tsx` | Standard settings section title/body/actions shell. |
| `FormFieldShell` | `src/components/shared/form-field-shell.tsx` | Label/help/error wrapper for Input/Select/Textarea/Switch patterns. |
| `SearchFilterBar` | `src/components/shared/search-filter-bar.tsx` | Standard search input + filter controls + reset action. |
| `ResourceListState` | `src/components/shared/resource-list-state.tsx` | Consistent loading/initial empty/filtered empty/error states. |
| `CountedTabs` | `src/components/shared/counted-tabs.tsx` | Counted tab UI backed by existing `LineTabs`/`Tabs`, replacing raw `role="tablist"` buttons. |
| `SidebarBrandHeader` | `src/components/layout/sidebar-brand-header.tsx` | Shared sidebar brand/logo/collapse trigger. |
| `SidebarUtilityFooter` | `src/components/layout/sidebar-utility-footer.tsx` | Shared theme/language/user footer. |
| `SidebarNavSection` | `src/components/layout/sidebar-nav-section.tsx` | Shared nav section from config. |

Do not make a single universal "ResourceEverything" component. Keep composition flexible:

- Resource list cards: `ResourceListCard`.
- Settings forms: `SettingsSectionCard` + `FormFieldShell`.
- CRUD tables: `DataTable`.
- Read-only metric tables: keep simple table or create `MetricTable` only after two usages.
- Domain-specific expandable tables, such as MCP tools, stay domain-specific.

## 8. Performance And Preflight Scope

Include performance checks now, but avoid deep optimization in the first PR series.

Do now:

- Verify Node 22 before frontend commands.
- Verify `node_modules` and `@rhwp/core/rhwp_bg.wasm` exist before `pnpm build`.
- Capture `next build` route size output after environment is fixed.
- Keep heavy artifact preview providers lazy:
  - `src/components/chat/artifacts/preview-registry.tsx`
  - `src/components/chat/markdown-code-block.tsx`
  - `src/components/chat/markdown-code-highlighter.tsx`
- Track Client Page count and prevent accidental growth.
- Track raw query key and broad invalidation usage.

Defer:

- Chat virtualization.
- Trace/debugger large-span virtualization.
- Bundle analyzer budget enforcement beyond route size baseline.
- Removing unused dependencies after depcheck/bundle verification.
- Reworking artifact preview providers beyond preserving lazy boundaries.

## 9. Implementation Tasks

### Task 1: Establish Frontend Preflight And Baseline

**Files:**

- Create: `frontend/scripts/preflight.mjs`
- Modify: `frontend/package.json`
- Read-only reference: `.node-version`
- Read-only reference: `frontend/scripts/copy-rhwp-wasm.mjs`

- [ ] **Step 1: Create the preflight script**

Create `frontend/scripts/preflight.mjs`:

```js
import { existsSync } from 'node:fs'
import { join } from 'node:path'
import { cwd, exit, version } from 'node:process'

const root = cwd()
const requiredNodeMajor = '22'
const currentMajor = version.replace(/^v/, '').split('.')[0]

const checks = [
  {
    name: 'node-major',
    ok: currentMajor === requiredNodeMajor,
    message: `Expected Node ${requiredNodeMajor}.x from ../.node-version, got ${version}.`,
  },
  {
    name: 'node-modules',
    ok: existsSync(join(root, 'node_modules')),
    message: 'Missing frontend/node_modules. Run `pnpm install --frozen-lockfile` from frontend.',
  },
  {
    name: 'rhwp-wasm',
    ok: existsSync(join(root, 'node_modules', '@rhwp', 'core', 'rhwp_bg.wasm')),
    message:
      'Missing node_modules/@rhwp/core/rhwp_bg.wasm. Reinstall dependencies before build/dev.',
  },
  {
    name: 'messages-ko',
    ok: existsSync(join(root, 'messages', 'ko.json')),
    message: 'Missing messages/ko.json.',
  },
  {
    name: 'messages-en',
    ok: existsSync(join(root, 'messages', 'en.json')),
    message: 'Missing messages/en.json.',
  },
]

let failed = false
for (const check of checks) {
  if (check.ok) {
    console.log(`ok ${check.name}`)
  } else {
    failed = true
    console.error(`fail ${check.name}: ${check.message}`)
  }
}

if (failed) exit(1)
```

- [ ] **Step 2: Add scripts**

Modify `frontend/package.json` scripts:

```json
{
  "scripts": {
    "preflight": "node scripts/preflight.mjs",
    "preflight:full": "pnpm preflight && pnpm lint && pnpm lint:i18n && pnpm lint:design-system && pnpm build"
  }
}
```

Keep existing scripts unchanged.

- [ ] **Step 3: Run expected failing preflight in the current worktree**

Run:

```bash
cd frontend
pnpm preflight
```

Expected in the current unprepared worktree:

```text
fail node-major: Expected Node 22.x ...
fail node-modules: Missing frontend/node_modules ...
fail rhwp-wasm: Missing node_modules/@rhwp/core/rhwp_bg.wasm ...
```

If Node 22 and dependencies are already installed at execution time, the expected result is all `ok`.

- [ ] **Step 4: Fix local environment before measuring performance**

Run with the user's preferred Node manager:

```bash
cd /Users/chester/.codex/worktrees/7ae4/natural-mold
node -v
cat .node-version
cd frontend
pnpm install --frozen-lockfile
pnpm preflight
```

Expected:

```text
v22.x.x
22
ok node-major
ok node-modules
ok rhwp-wasm
ok messages-ko
ok messages-en
```

- [ ] **Step 5: Capture baseline**

Run:

```bash
cd frontend
pnpm lint
pnpm lint:i18n
pnpm lint:design-system
pnpm build | tee ../output/frontend-build-baseline-2026-06-17.txt
```

Expected:

- Lint commands pass.
- Build succeeds.
- `output/frontend-build-baseline-2026-06-17.txt` contains route size output.

- [ ] **Step 6: Commit**

```bash
git add frontend/scripts/preflight.mjs frontend/package.json
git commit -m "chore(frontend): add preflight baseline checks"
```

### Task 2: Add Architecture Guard In Report Mode

**Files:**

- Create: `frontend/scripts/check-frontend-architecture.mjs`
- Modify: `frontend/package.json`
- Modify later in Task 12: `frontend/AGENTS.md`

- [ ] **Step 1: Create the report script**

Create `frontend/scripts/check-frontend-architecture.mjs`:

```js
import { readFileSync, readdirSync, statSync } from 'node:fs'
import { join, relative } from 'node:path'
import { exit } from 'node:process'

const root = process.cwd()
const srcRoot = join(root, 'src')
const strict = process.argv.includes('--strict')

const allow = {
  dialogContent: new Set([
    'src/components/ui/dialog.tsx',
    'src/components/shared/dialog-shell.tsx',
    'src/components/shared/delete-confirm-dialog.tsx',
  ]),
  tablist: new Set([
    'src/components/shared/counted-tabs.tsx',
    'src/components/shared/resource-layout.tsx',
    'src/components/ui/tabs.tsx',
    'src/components/ui/line-tabs.tsx',
  ]),
}

function walk(dir) {
  const out = []
  for (const entry of readdirSync(dir)) {
    const full = join(dir, entry)
    const stat = statSync(full)
    if (stat.isDirectory()) {
      if (entry === 'node_modules' || entry === '.next') continue
      out.push(...walk(full))
    } else if (/\.(tsx|ts)$/.test(entry)) {
      out.push(full)
    }
  }
  return out
}

const files = walk(srcRoot)
const issues = []

for (const file of files) {
  const rel = relative(root, file)
  const text = readFileSync(file, 'utf8')

  if (
    text.includes("from '@/components/ui/dialog'") &&
    /DialogContent|Dialog\b/.test(text) &&
    !allow.dialogContent.has(rel)
  ) {
    issues.push({
      rel,
      rule: 'dialog-shell',
      message: 'Use DialogShell for new dialogs instead of direct Dialog/DialogContent.',
    })
  }

  if (/role=["']tablist["']/.test(text) && !allow.tablist.has(rel)) {
    issues.push({
      rel,
      rule: 'tabs',
      message: 'Use CountedTabs/LineTabs/Tabs instead of raw role="tablist".',
    })
  }

  if (/^['"]use client['"]/.test(text) && /src\/app\/.+\/page\.tsx$/.test(rel)) {
    issues.push({
      rel,
      rule: 'client-page',
      message: 'Prefer a Server Component page wrapper plus a route-local Client Component.',
    })
  }

  if (/queryKey:\s*\[[^\]]+]/.test(text) && !/Keys\s*=|queryKeys\s*=/.test(text)) {
    issues.push({
      rel,
      rule: 'raw-query-key',
      message: 'Prefer feature query key factories for new TanStack Query keys.',
    })
  }
}

for (const issue of issues) {
  console.log(`${issue.rule}: ${issue.rel} - ${issue.message}`)
}

console.log(`frontend architecture issues: ${issues.length}`)

if (strict && issues.length > 0) exit(1)
```

- [ ] **Step 2: Add scripts**

Modify `frontend/package.json` scripts:

```json
{
  "scripts": {
    "lint:frontend-architecture": "node scripts/check-frontend-architecture.mjs",
    "lint:frontend-architecture:strict": "node scripts/check-frontend-architecture.mjs --strict"
  }
}
```

- [ ] **Step 3: Run report mode**

Run:

```bash
cd frontend
pnpm lint:frontend-architecture
```

Expected:

- Command exits 0.
- It reports existing direct dialog, raw tablist, Client Page, and raw query-key candidates.

- [ ] **Step 4: Do not enable strict yet**

Strict mode should be enabled only after Tasks 3-11 migrate the existing violations or add justified allowlist entries.

- [ ] **Step 5: Commit**

```bash
git add frontend/scripts/check-frontend-architecture.mjs frontend/package.json
git commit -m "chore(frontend): report architecture guard candidates"
```

### Task 3: Build Shared UI Grammar

**Files:**

- Create: `frontend/src/components/shared/settings-section-card.tsx`
- Create: `frontend/src/components/shared/form-field-shell.tsx`
- Create: `frontend/src/components/shared/search-filter-bar.tsx`
- Create: `frontend/src/components/shared/resource-list-state.tsx`
- Create: `frontend/src/components/shared/counted-tabs.tsx`
- Test: `frontend/src/components/shared/__tests__/settings-section-card.test.tsx`
- Test: `frontend/src/components/shared/__tests__/form-field-shell.test.tsx`
- Test: `frontend/src/components/shared/__tests__/resource-list-state.test.tsx`

- [ ] **Step 1: Write tests for common shells**

Create focused tests that assert semantics rather than snapshots:

```tsx
import { render, screen } from '@testing-library/react'
import { SettingsSectionCard } from '../settings-section-card'

test('renders settings section title, description, and actions', () => {
  render(
    <SettingsSectionCard
      title="모델 설정"
      description="기본 모델과 자격증명을 관리합니다."
      actions={<button type="button">저장</button>}
    >
      <div>body</div>
    </SettingsSectionCard>,
  )

  expect(screen.getByRole('heading', { name: '모델 설정' })).toBeInTheDocument()
  expect(screen.getByText('기본 모델과 자격증명을 관리합니다.')).toBeInTheDocument()
  expect(screen.getByRole('button', { name: '저장' })).toBeInTheDocument()
  expect(screen.getByText('body')).toBeInTheDocument()
})
```

- [ ] **Step 2: Implement `SettingsSectionCard`**

Use Moldy tokens/classes only:

```tsx
import type { ReactNode } from 'react'

import { cn } from '@/lib/utils'

interface SettingsSectionCardProps {
  title: ReactNode
  description?: ReactNode
  actions?: ReactNode
  children: ReactNode
  className?: string
}

export function SettingsSectionCard({
  title,
  description,
  actions,
  children,
  className,
}: SettingsSectionCardProps) {
  return (
    <section className={cn('moldy-card p-5', className)}>
      <div className="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
        <div className="min-w-0 space-y-1">
          <h2 className="moldy-ui-subtitle text-foreground">{title}</h2>
          {description ? <p className="moldy-ui-copy text-muted-foreground">{description}</p> : null}
        </div>
        {actions ? <div className="flex shrink-0 items-center gap-2">{actions}</div> : null}
      </div>
      <div className="mt-5">{children}</div>
    </section>
  )
}
```

- [ ] **Step 3: Implement `FormFieldShell`**

```tsx
import type { ReactNode } from 'react'

import { cn } from '@/lib/utils'

interface FormFieldShellProps {
  id?: string
  label: ReactNode
  description?: ReactNode
  error?: ReactNode
  required?: boolean
  children: ReactNode
  className?: string
}

export function FormFieldShell({
  id,
  label,
  description,
  error,
  required,
  children,
  className,
}: FormFieldShellProps) {
  const descriptionId = id ? `${id}-description` : undefined
  const errorId = id ? `${id}-error` : undefined

  return (
    <div className={cn('space-y-1.5', className)}>
      <label htmlFor={id} className="moldy-ui-label text-foreground">
        {label}
        {required ? <span className="text-status-danger"> *</span> : null}
      </label>
      {description ? (
        <p id={descriptionId} className="moldy-ui-caption text-muted-foreground">
          {description}
        </p>
      ) : null}
      {children}
      {error ? (
        <p id={errorId} className="moldy-ui-caption text-status-danger">
          {error}
        </p>
      ) : null}
    </div>
  )
}
```

- [ ] **Step 4: Implement `SearchFilterBar`**

Use existing `SearchInput` and layout composition:

```tsx
import type { ReactNode } from 'react'

import { Button } from '@/components/ui/button'
import { SearchInput } from '@/components/shared/search-input'
import { cn } from '@/lib/utils'

interface SearchFilterBarProps {
  value: string
  onValueChange: (value: string) => void
  searchLabel: string
  resetLabel?: string
  onReset?: () => void
  filters?: ReactNode
  actions?: ReactNode
  className?: string
}

export function SearchFilterBar({
  value,
  onValueChange,
  searchLabel,
  resetLabel,
  onReset,
  filters,
  actions,
  className,
}: SearchFilterBarProps) {
  return (
    <div className={cn('flex flex-col gap-3 md:flex-row md:items-center md:justify-between', className)}>
      <div className="flex min-w-0 flex-1 flex-col gap-2 sm:flex-row sm:items-center">
        <SearchInput value={value} onChange={onValueChange} aria-label={searchLabel} />
        {filters}
        {onReset && resetLabel ? (
          <Button type="button" variant="ghost" size="sm" onClick={onReset}>
            {resetLabel}
          </Button>
        ) : null}
      </div>
      {actions ? <div className="flex shrink-0 items-center gap-2">{actions}</div> : null}
    </div>
  )
}
```

- [ ] **Step 5: Implement `ResourceListState`**

```tsx
import type { ReactNode } from 'react'

import { Button } from '@/components/ui/button'
import { EmptyState } from '@/components/shared/empty-state'
import { ErrorState } from '@/components/shared/error-state'

interface ResourceListStateProps {
  loading?: boolean
  error?: boolean
  isFiltered?: boolean
  skeleton: ReactNode
  emptyTitle: string
  emptyDescription?: string
  filteredEmptyTitle: string
  filteredEmptyDescription?: string
  retryLabel?: string
  onRetry?: () => void
}

export function ResourceListState({
  loading,
  error,
  isFiltered,
  skeleton,
  emptyTitle,
  emptyDescription,
  filteredEmptyTitle,
  filteredEmptyDescription,
  retryLabel,
  onRetry,
}: ResourceListStateProps) {
  if (loading) return <>{skeleton}</>
  if (error) return <ErrorState onRetry={onRetry} />

  if (isFiltered) {
    return (
      <EmptyState title={filteredEmptyTitle} description={filteredEmptyDescription}>
        {onRetry && retryLabel ? (
          <Button type="button" variant="outline" onClick={onRetry}>
            {retryLabel}
          </Button>
        ) : null}
      </EmptyState>
    )
  }

  return <EmptyState title={emptyTitle} description={emptyDescription} />
}
```

- [ ] **Step 6: Implement `CountedTabs` using existing line tabs**

```tsx
import { LineTabs, LineTabsList, LineTabsTrigger } from '@/components/ui/line-tabs'

export interface CountedTabItem {
  value: string
  label: string
  count?: number
}

interface CountedTabsProps {
  value: string
  onValueChange: (value: string) => void
  tabs: CountedTabItem[]
  ariaLabel: string
}

export function CountedTabs({ value, onValueChange, tabs, ariaLabel }: CountedTabsProps) {
  return (
    <LineTabs value={value} onValueChange={onValueChange} aria-label={ariaLabel}>
      <LineTabsList>
        {tabs.map((tab) => (
          <LineTabsTrigger key={tab.value} value={tab.value}>
            <span>{tab.label}</span>
            {typeof tab.count === 'number' ? (
              <span className="moldy-ui-caption tabular-nums text-muted-foreground">
                {tab.count}
              </span>
            ) : null}
          </LineTabsTrigger>
        ))}
      </LineTabsList>
    </LineTabs>
  )
}
```

- [ ] **Step 7: Run targeted tests**

```bash
cd frontend
pnpm exec vitest run src/components/shared/__tests__/settings-section-card.test.tsx src/components/shared/__tests__/form-field-shell.test.tsx src/components/shared/__tests__/resource-list-state.test.tsx
pnpm lint:design-system
pnpm lint:i18n
```

Expected: all pass.

- [ ] **Step 8: Commit**

```bash
git add frontend/src/components/shared frontend/messages frontend/package.json
git commit -m "feat(frontend): add shared resource and settings primitives"
```

### Task 4: Commonize Sidebar Header, Footer, And Nav Sections

**Files:**

- Create: `frontend/src/components/layout/sidebar-brand-header.tsx`
- Create: `frontend/src/components/layout/sidebar-nav-section.tsx`
- Create: `frontend/src/components/layout/sidebar-utility-footer.tsx`
- Modify: `frontend/src/components/layout/app-sidebar.tsx`
- Modify: `frontend/src/components/layout/settings-sidebar.tsx`
- Existing reference: `frontend/src/components/layout/app-sidebar-footer.tsx`

- [ ] **Step 1: Extract shared brand header**

Move duplicated logo/collapse trigger from `app-sidebar.tsx` and `settings-sidebar.tsx` into `SidebarBrandHeader`.

Requirements:

- Uses `next/image`.
- Supports expanded and collapsed sidebar.
- Receives translated `toggleLabel`.
- Does not hardcode route-specific nav.

- [ ] **Step 2: Extract shared utility footer**

Use existing `AppSidebarFooter` behavior as the canonical footer.

Requirements:

- Theme toggle.
- Locale dropdown.
- User menu/skeleton.
- No duplicate footer implementation in `settings-sidebar.tsx`.

- [ ] **Step 3: Extract nav section renderer**

Create a config-driven renderer:

```ts
export interface SidebarNavItemConfig {
  href: string
  label: string
  icon: LucideIcon
  exact?: boolean
}

export interface SidebarNavSectionConfig {
  label: string
  items: SidebarNavItemConfig[]
}
```

Use this for settings nav first because it is mostly static.

- [ ] **Step 4: Verify behavior manually**

Start dev server after environment is prepared:

```bash
cd frontend
NEXT_PUBLIC_API_BASE_URL=http://localhost:8001 pnpm dev -- --port 3000
```

Manual checks:

- App sidebar expands/collapses.
- Settings sidebar expands/collapses.
- Theme toggle still works.
- Locale dropdown still works.
- User menu still works.
- Settings active nav item is preserved.

- [ ] **Step 5: Run checks**

```bash
cd frontend
pnpm lint
pnpm lint:i18n
pnpm lint:design-system
pnpm exec vitest run src/components/layout
```

Expected: all pass or no matching layout tests are found; if no tests exist, add a small render test for `SidebarBrandHeader`.

- [ ] **Step 6: Commit**

```bash
git add frontend/src/components/layout frontend/messages
git commit -m "refactor(frontend): share sidebar shell primitives"
```

### Task 5: Convert Resource Pages To Route-Local Client Islands

**Files:**

- Modify: `frontend/src/app/mcp-servers/page.tsx`
- Create: `frontend/src/app/mcp-servers/_components/mcp-servers-page-client.tsx`
- Move: `frontend/src/components/mcp/*` to `frontend/src/app/mcp-servers/_components/*`
- Modify: `frontend/src/app/tools/page.tsx`
- Create: `frontend/src/app/tools/_components/tools-page-client.tsx`
- Move: `frontend/src/components/tool/*` to `frontend/src/app/tools/_components/*`
- Modify: `frontend/src/app/skills/page.tsx`
- Create: `frontend/src/app/skills/_components/skills-page-client.tsx`
- Move selected skill list/dialog coordinator files to `frontend/src/app/skills/_components/*`

- [ ] **Step 1: Start with MCP servers**

Change `app/mcp-servers/page.tsx` into a Server Component wrapper:

```tsx
import { McpServersPageClient } from './_components/mcp-servers-page-client'

export default function McpServersPage() {
  return <McpServersPageClient />
}
```

Move the current page body into `_components/mcp-servers-page-client.tsx` and keep `'use client'` there.

- [ ] **Step 2: Move MCP route-only components**

Move files and update imports:

```bash
mkdir -p frontend/src/app/mcp-servers/_components
git mv frontend/src/components/mcp/mcp-server-wizard.tsx frontend/src/app/mcp-servers/_components/mcp-server-wizard.tsx
git mv frontend/src/components/mcp/mcp-server-detail-dialog.tsx frontend/src/app/mcp-servers/_components/mcp-server-detail-dialog.tsx
git mv frontend/src/components/mcp/mcp-import-dialog.tsx frontend/src/app/mcp-servers/_components/mcp-import-dialog.tsx
git mv frontend/src/components/mcp/mcp-tool-table.tsx frontend/src/app/mcp-servers/_components/mcp-tool-table.tsx
```

Use direct relative imports within the route folder.

- [ ] **Step 3: Apply `ResourcePage`, `SearchFilterBar`, and `ResourceListState` to MCP page**

Replace custom toolbar/search/empty/loading handling with shared primitives where behavior matches.

Do not force `McpToolTable` into generic `DataTable`; it has expandable schema rows and belongs in the MCP route.

- [ ] **Step 4: Repeat for Tools page**

Move current interactive body to `app/tools/_components/tools-page-client.tsx`.

Move:

```bash
mkdir -p frontend/src/app/tools/_components
git mv frontend/src/components/tool/tool-catalog.tsx frontend/src/app/tools/_components/tool-catalog.tsx
git mv frontend/src/components/tool/tool-create-dialog.tsx frontend/src/app/tools/_components/tool-create-dialog.tsx
git mv frontend/src/components/tool/tool-detail-dialog.tsx frontend/src/app/tools/_components/tool-detail-dialog.tsx
```

- [ ] **Step 5: Repeat for Skills page**

Move list-page-only files:

```bash
mkdir -p frontend/src/app/skills/_components
git mv frontend/src/components/skill/skill-card.tsx frontend/src/app/skills/_components/skill-card.tsx
git mv frontend/src/components/skill/skill-page-dialogs.tsx frontend/src/app/skills/_components/skill-page-dialogs.tsx
git mv frontend/src/components/skill/skill-state-filter-chips.tsx frontend/src/app/skills/_components/skill-state-filter-chips.tsx
```

Keep package editor/detail/evaluation components in `components/skill` until a separate skill detail feature split.

- [ ] **Step 6: Run targeted checks**

```bash
cd frontend
pnpm lint
pnpm lint:i18n
pnpm lint:design-system
pnpm lint:frontend-architecture
pnpm exec playwright test e2e/mcp-server-wizard.spec.ts e2e/tools-catalog.spec.ts --project=chromium --workers=1
```

Expected:

- Lint/design/i18n pass.
- Architecture report shows fewer Client Page and global route-only component issues.
- MCP and Tools E2E pass.

- [ ] **Step 7: Commit**

```bash
git add frontend/src/app/mcp-servers frontend/src/app/tools frontend/src/app/skills frontend/src/components
git commit -m "refactor(frontend): colocate resource route clients"
```

### Task 6: Convert Settings Pages To Shared Settings Grammar

**Files:**

- Modify: `frontend/src/app/settings/memory/page.tsx`
- Modify: `frontend/src/app/settings/schedules/page.tsx`
- Modify: `frontend/src/app/settings/system-llm/page.tsx`
- Modify: `frontend/src/app/settings/usage/page.tsx`
- Modify: `frontend/src/app/settings/models/page.tsx`
- Modify: `frontend/src/app/settings/_components/settings-shell.tsx`

- [ ] **Step 1: Standardize `SettingsShell`**

Ensure `SettingsShell` owns page width and vertical rhythm only. It should not know domain logic.

Add props only if needed:

```tsx
interface SettingsShellProps {
  children: ReactNode
  wide?: boolean
  className?: string
}
```

- [ ] **Step 2: Replace repeated raw settings cards**

Use `SettingsSectionCard` for:

- Memory settings sections in `settings/memory/page.tsx`.
- System LLM role cards in `settings/system-llm/page.tsx`.
- Usage filter/report panels in `settings/usage/page.tsx`.
- Model management panels where raw `Card` currently acts as a settings section.

- [ ] **Step 3: Replace local field wrappers**

In `settings/memory/page.tsx`, remove local `ToggleField` and `SelectField` only after equivalent `FormFieldShell` composition is in place.

Do not introduce state resets in `useEffect`; follow `frontend/AGENTS.md` React 19 remount/derived-state rules.

- [ ] **Step 4: Convert schedules page list state**

In `settings/schedules/page.tsx`, use:

- `SearchFilterBar`
- `ResourceListState`
- `ResourceListCard` or a schedule-specific card in `features/schedules`

- [ ] **Step 5: Verify settings screens**

Run:

```bash
cd frontend
pnpm lint
pnpm lint:i18n
pnpm lint:design-system
pnpm exec playwright test e2e/settings.spec.ts e2e/models.spec.ts --project=chromium --workers=1
```

If these exact specs do not exist, run the nearest settings/model E2E specs listed by:

```bash
find e2e -maxdepth 1 -name '*settings*.spec.ts' -o -name '*model*.spec.ts'
```

- [ ] **Step 6: Commit**

```bash
git add frontend/src/app/settings frontend/src/components/shared frontend/messages
git commit -m "refactor(frontend): standardize settings surface grammar"
```

### Task 7: Unify Schedule UI Into A Feature Module

**Files:**

- Create directory: `frontend/src/features/schedules/`
- Move/split: `frontend/src/components/agent/visual-settings/dialogs/schedule-dialog.tsx`
- Modify: `frontend/src/app/settings/schedules/page.tsx`
- Modify: `frontend/src/components/agent/visual-settings/nodes/schedule-node.tsx`
- Modify: `frontend/src/components/agent/visual-settings/visual-settings-flow.tsx`

- [ ] **Step 1: Create feature module skeleton**

Create:

```text
frontend/src/features/schedules/
├── components/
│   ├── schedule-dialog.tsx
│   ├── schedule-form.tsx
│   ├── schedule-list-card.tsx
│   └── schedule-run-list.tsx
├── lib/
│   ├── cron-labels.ts
│   └── schedule-form-state.ts
└── query-keys.ts
```

- [ ] **Step 2: Move pure cron/form helpers first**

Extract from `schedule-dialog.tsx`:

- Cron parse/format helpers.
- Weekday constants.
- Initial form state builder.
- Payload builder for create/update.

Add unit tests for helper behavior:

```bash
cd frontend
pnpm exec vitest run src/features/schedules/lib
```

- [ ] **Step 3: Split dialog into form and shell**

`schedule-dialog.tsx` should own open/close/mutation wiring.

`schedule-form.tsx` should own fields and validation display.

Use `FormFieldShell` for labels, descriptions, and errors.

- [ ] **Step 4: Share schedule card/list between settings and visual settings**

Use `schedule-list-card.tsx` in `settings/schedules/page.tsx`.

Use the same status label helpers in `schedule-node.tsx`.

- [ ] **Step 5: Run focused checks**

```bash
cd frontend
pnpm exec vitest run src/features/schedules
pnpm lint
pnpm lint:i18n
pnpm lint:design-system
```

- [ ] **Step 6: Commit**

```bash
git add frontend/src/features/schedules frontend/src/app/settings/schedules/page.tsx frontend/src/components/agent/visual-settings frontend/messages
git commit -m "refactor(frontend): extract schedule feature UI"
```

### Task 8: DialogShell Migration Pass

**Files:**

- Modify: `frontend/src/app/settings/agent-api/_components/api-key-create-dialog.tsx`
- Modify: `frontend/src/app/settings/agent-api/_components/api-key-created-dialog.tsx`
- Review: `frontend/src/app/agents/[agentId]/settings/page.tsx`
- Review: `frontend/src/components/skill/skill-evaluation-estimate-dialog.tsx`

- [ ] **Step 1: Migrate API key create dialog**

Replace direct `DialogContent` usage with `DialogShell`.

Requirements:

- Size uses existing dialog token names.
- Header/body/footer use `DialogShell.Header`, `DialogShell.Body`, `DialogShell.Footer`.
- No raw max-height/max-width classes.
- Copy remains in `messages/ko.json` and `messages/en.json`.

- [ ] **Step 2: Migrate API key created dialog**

Same rules as Step 1.

- [ ] **Step 3: Review AlertDialog usages**

Do not blindly replace all `AlertDialogContent`.

Allowed:

- `components/shared/delete-confirm-dialog.tsx` can stay as the shared destructive-confirm primitive.

Migration candidate:

- Agent settings delete confirmation should use `DeleteConfirmDialog` if the behavior matches.
- Skill evaluation estimate dialog can stay alert-style if it is a true confirmation, but should use a shared confirmation wrapper if duplicated.

- [ ] **Step 4: Run guard**

```bash
cd frontend
pnpm lint:frontend-architecture
pnpm lint
pnpm lint:design-system
```

Expected:

- Direct dialog issues decrease.
- No design-system violations.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/app/settings/agent-api frontend/src/app/agents frontend/src/components/skill frontend/messages
git commit -m "refactor(frontend): migrate settings dialogs to DialogShell"
```

### Task 9: Query Key Factory And Invalidation Cleanup

**Files:**

- Modify: `frontend/src/lib/hooks/use-marketplace.ts`
- Modify: `frontend/src/lib/hooks/use-skills.ts`
- Modify: `frontend/src/lib/hooks/use-tools.ts`
- Modify: `frontend/src/lib/hooks/use-triggers.ts`
- Modify: `frontend/src/lib/chat/use-chat-runtime.ts`
- Optional create: `frontend/src/lib/query-keys/index.ts`

- [ ] **Step 1: Inventory raw keys**

Run:

```bash
cd frontend
rg "queryKey:\\s*\\[|invalidateQueries\\(\\{ queryKey: \\[" src/lib src/app src/components -n
```

Record the before count in the commit message body.

- [ ] **Step 2: Add or normalize key factories**

Pattern:

```ts
export const skillKeys = {
  all: ['skills'] as const,
  list: (params?: unknown) => ['skills', params ?? {}] as const,
  detail: (id: string) => ['skills', id] as const,
  files: (id: string) => ['skills', id, 'files'] as const,
  content: (id: string) => ['skills', id, 'content'] as const,
}
```

Do this inside each hook file first. Only move to `lib/query-keys` if the same key factory is needed by multiple domains.

- [ ] **Step 3: Replace broad invalidations where safe**

Examples:

- `qc.invalidateQueries({ queryKey: ['skills'] })` becomes `qc.invalidateQueries({ queryKey: skillKeys.all })`.
- `qc.invalidateQueries({ queryKey: ['agents'] })` remains broad only when an operation can change agent list membership, favorite, unread, or trigger summary.

- [ ] **Step 4: Run affected tests**

```bash
cd frontend
pnpm exec vitest run src/lib/hooks src/lib/chat
pnpm lint:frontend-architecture
pnpm lint
```

- [ ] **Step 5: Commit**

```bash
git add frontend/src/lib frontend/src/app frontend/src/components
git commit -m "refactor(frontend): normalize query key factories"
```

### Task 10: Route Loading And Error Boundaries

**Files:**

- Create: `frontend/src/app/mcp-servers/loading.tsx`
- Create: `frontend/src/app/tools/loading.tsx`
- Create: `frontend/src/app/skills/loading.tsx`
- Create: `frontend/src/app/settings/loading.tsx`
- Create: `frontend/src/app/marketplace/loading.tsx`
- Create selectively: route `error.tsx` files where client recovery is needed.
- Reuse: `frontend/src/components/shared/error-state.tsx`

- [ ] **Step 1: Add route loading skeletons for migrated resource routes**

Use shared skeleton/card classes only:

```tsx
import { ResourceGrid } from '@/components/shared/resource-layout'
import { Skeleton } from '@/components/ui/skeleton'

export default function Loading() {
  return (
    <div className="moldy-app-surface flex min-h-0 flex-1 flex-col overflow-auto p-6">
      <div className="mx-auto flex w-full max-w-[1180px] flex-col gap-5">
        <Skeleton className="h-20 w-full" />
        <ResourceGrid>
          {Array.from({ length: 6 }).map((_, index) => (
            <Skeleton key={index} className="h-44 w-full" />
          ))}
        </ResourceGrid>
      </div>
    </div>
  )
}
```

Adjust skeleton height per route only when necessary.

- [ ] **Step 2: Add error boundary where recovery matters**

Create `error.tsx` only in routes that can recover with a retry:

```tsx
'use client'

import { ErrorState } from '@/components/shared/error-state'

export default function Error({ reset }: { error: Error; reset: () => void }) {
  return (
    <div className="moldy-app-surface flex min-h-0 flex-1 items-center justify-center p-6">
      <ErrorState onRetry={reset} />
    </div>
  )
}
```

- [ ] **Step 3: Run build**

```bash
cd frontend
pnpm build
```

Expected: build passes and routes include loading/error boundaries.

- [ ] **Step 4: Commit**

```bash
git add frontend/src/app
git commit -m "feat(frontend): add resource route loading boundaries"
```

### Task 11: Split MCP Wizard State And Rendering

**Files:**

- Modify: `frontend/src/app/mcp-servers/_components/mcp-server-wizard.tsx`
- Create: `frontend/src/app/mcp-servers/_components/mcp-wizard-form-state.ts`
- Create: `frontend/src/app/mcp-servers/_components/mcp-wizard-transport-section.tsx`
- Create: `frontend/src/app/mcp-servers/_components/mcp-wizard-credentials-section.tsx`
- Create: `frontend/src/app/mcp-servers/_components/mcp-wizard-probe-section.tsx`
- Test: `frontend/src/app/mcp-servers/_components/__tests__/mcp-wizard-form-state.test.ts`

- [ ] **Step 1: Extract pure state helpers**

Move form initialization, env/header transforms, registry payload mapping, and validation into `mcp-wizard-form-state.ts`.

Unit test:

```ts
import { buildMcpServerPayload, createInitialMcpWizardState } from '../mcp-wizard-form-state'

test('builds streamable http payload with env and headers', () => {
  const state = createInitialMcpWizardState()
  const payload = buildMcpServerPayload({
    ...state,
    name: 'Docs',
    transport: 'streamable_http',
    url: 'https://example.com/mcp',
    headers: [{ key: 'X-Test', value: 'ok' }],
  })

  expect(payload).toMatchObject({
    name: 'Docs',
    transport: 'streamable_http',
    url: 'https://example.com/mcp',
    headers: { 'X-Test': 'ok' },
  })
})
```

- [ ] **Step 2: Split rendering sections**

Keep the parent wizard responsible for:

- Open/close.
- Mutation.
- OAuth completion message listener.
- Probe action wiring.

Move pure field groups to section components.

- [ ] **Step 3: Run existing MCP E2E**

```bash
cd frontend
pnpm exec vitest run src/app/mcp-servers/_components
pnpm exec playwright test e2e/mcp-server-wizard.spec.ts --project=chromium --workers=1
pnpm lint
pnpm lint:i18n
pnpm lint:design-system
```

- [ ] **Step 4: Commit**

```bash
git add frontend/src/app/mcp-servers/_components frontend/messages
git commit -m "refactor(frontend): split MCP wizard state and sections"
```

### Task 12: Update AGENTS Rules And Enable Architecture Guard Strict Mode

**Files:**

- Modify: `frontend/AGENTS.md`
- No direct edit required: `frontend/CLAUDE.md`
- Modify: `frontend/package.json`
- Modify: `frontend/scripts/check-frontend-architecture.mjs` allowlist if justified

- [ ] **Step 1: Add folder architecture rules to `frontend/AGENTS.md`**

Append:

```markdown
## Frontend folder architecture

- `app/**/page.tsx` should be a Server Component wrapper unless the entire route genuinely requires browser APIs at the page boundary.
- Route-only UI lives under that route's `_components/`, `_hooks/`, or `_lib/`.
- Components used by multiple routes in one product domain live under `src/features/<domain>/`.
- Components used across unrelated domains live under `src/components/shared/`.
- `src/components/ui/` is for shadcn/base primitives only and must not import app/domain hooks.
- Do not add new barrel exports for frontend modules unless the importing ergonomics clearly outweigh bundle/dev-time costs.
```

- [ ] **Step 2: Add common UI rules**

Append:

```markdown
## Frontend commonization rules

- Resource list pages use `ResourcePage`, `ResourcePanel`, `ResourceGrid`, `ResourceListCard`, `SearchFilterBar`, and `ResourceListState` before creating route-specific shells.
- Settings pages use `SettingsShell`, `SettingsSectionCard`, and `FormFieldShell` before creating local card/field wrappers.
- New counted tabs use `CountedTabs` or existing `LineTabs`; do not hand-roll `role="tablist"` buttons.
- New dialogs use `DialogShell`; direct `DialogContent` is limited to `components/ui/dialog.tsx`, `components/shared/dialog-shell.tsx`, and shared confirmation primitives.
- CRUD tables may use `DataTable`; domain-specific expandable tables can stay local. Do not force metric/read-only tables into `DataTable` unless behavior is reused.
```

- [ ] **Step 3: Add performance/preflight rules**

Append:

```markdown
## Frontend preflight and performance rules

- Run `pnpm preflight` before build/dev diagnostics. The project expects Node 22 and installed frontend dependencies.
- After frontend refactors run `pnpm lint`, `pnpm lint:i18n`, `pnpm lint:design-system`, and `pnpm lint:frontend-architecture`.
- Do not add a new page-level `'use client'` without explaining why a Server Component wrapper plus Client island is insufficient.
- Keep heavy artifact viewers, markdown highlighters, document parsers, Mermaid, HWP/DOCX/XLSX/PPTX viewers, and similar libraries behind lazy/dynamic imports.
- New TanStack Query keys should be created through feature key factories, not ad hoc raw arrays inside components.
```

- [ ] **Step 4: Enable strict architecture guard after migrations**

After Tasks 3-11 have reduced existing issues or added explicit allowlist entries:

```json
{
  "scripts": {
    "preflight:full": "pnpm preflight && pnpm lint && pnpm lint:i18n && pnpm lint:design-system && pnpm lint:frontend-architecture:strict && pnpm build"
  }
}
```

- [ ] **Step 5: Run full frontend gate**

```bash
cd frontend
pnpm preflight:full
```

Expected: passes in a prepared Node 22 environment.

- [ ] **Step 6: Commit**

```bash
git add frontend/AGENTS.md frontend/package.json frontend/scripts/check-frontend-architecture.mjs
git commit -m "docs(frontend): document architecture and performance guardrails"
```

### Task 13: Final Regression Pass

**Files:** no planned source edits unless failures are found.

- [ ] **Step 1: Static checks**

```bash
cd frontend
pnpm preflight
pnpm lint
pnpm lint:i18n
pnpm lint:design-system
pnpm lint:frontend-architecture:strict
pnpm format:check
```

- [ ] **Step 2: Unit tests**

```bash
cd frontend
pnpm test
```

- [ ] **Step 3: Build**

```bash
cd frontend
pnpm build
```

- [ ] **Step 4: E2E smoke**

Use the worktree port/CORS rules from the root `AGENTS.md`.

Recommended smoke set:

```bash
cd frontend
pnpm exec playwright test e2e/mcp-server-wizard.spec.ts e2e/tools-catalog.spec.ts --project=chromium --workers=1
pnpm exec playwright test e2e/chat-langgraph-v3.spec.ts --project=chromium --workers=1
```

If settings/model specs exist, include them:

```bash
find e2e -maxdepth 1 -name '*settings*.spec.ts' -o -name '*model*.spec.ts' -o -name '*skill*.spec.ts'
```

- [ ] **Step 5: Architecture delta check**

Run:

```bash
cd frontend
node - <<'NODE'
const fs=require('fs'); const path=require('path');
function files(dir){let out=[]; for(const e of fs.readdirSync(dir,{withFileTypes:true})){const p=path.join(dir,e.name); if(e.isDirectory()) out=out.concat(files(p)); else if(/\.(tsx|ts)$/.test(e.name)) out.push(p)} return out}
const all=files('src');
const pages=files('src/app').filter(f=>f.endsWith('/page.tsx'));
const clientFiles=all.filter(f=>/^['"]use client['"]/.test(fs.readFileSync(f,'utf8')));
const clientPages=pages.filter(f=>/^['"]use client['"]/.test(fs.readFileSync(f,'utf8')));
console.log({ totalTsTsx: all.length, clientFiles: clientFiles.length, pages: pages.length, clientPages: clientPages.length });
NODE
```

Expected:

- Client Page count decreases from the baseline of 28, or every remaining Client Page has a documented reason.
- Global route-only component count decreases.

- [ ] **Step 6: Commit final fixes**

```bash
git status --short
git add frontend docs
git commit -m "refactor(frontend): complete modernization guardrails"
```

## 10. Testing Matrix

Run these after each task group:

| Scope | Command | When |
| --- | --- | --- |
| Environment | `cd frontend && pnpm preflight` | Before build/dev diagnostics. |
| Lint | `cd frontend && pnpm lint` | Every task. |
| i18n | `cd frontend && pnpm lint:i18n` | Any UI copy/aria/title/toast change. |
| Design system | `cd frontend && pnpm lint:design-system` | Any UI/class change. |
| Architecture report | `cd frontend && pnpm lint:frontend-architecture` | Every migration task. |
| Architecture strict | `cd frontend && pnpm lint:frontend-architecture:strict` | After Task 12. |
| Shared component unit | `cd frontend && pnpm exec vitest run src/components/shared` | After Task 3. |
| Schedule unit | `cd frontend && pnpm exec vitest run src/features/schedules` | After Task 7. |
| MCP wizard | `cd frontend && pnpm exec vitest run src/app/mcp-servers/_components` | After Task 11. |
| Full unit | `cd frontend && pnpm test` | Final regression. |
| Build | `cd frontend && pnpm build` | After route boundary changes and final. |
| E2E smoke | `cd frontend && pnpm exec playwright test e2e/mcp-server-wizard.spec.ts e2e/tools-catalog.spec.ts --project=chromium --workers=1` | After resource route migrations. |
| Chat smoke | `cd frontend && NEXT_PUBLIC_CHAT_RUNTIME=langgraph_v3 pnpm exec playwright test e2e/chat-langgraph-v3.spec.ts --project=chromium --workers=1` | Final regression or chat-touching changes. |

## 11. Completion Criteria

This refactor is complete when:

- `pnpm preflight:full` passes in Node 22 with dependencies installed.
- At least MCP, Tools, and Skills pages use Server wrapper + route-local Client Component.
- Sidebar header/footer/nav duplication is removed.
- Settings pages use the shared settings section/field grammar for new or touched sections.
- Direct `DialogContent` usage is either migrated or allowlisted with a reason.
- `frontend/AGENTS.md` contains folder architecture, commonization, and preflight/performance rules.
- Architecture guard strict mode is part of the full preflight gate, after allowlists are intentional.
- Route loading states exist for the migrated resource/settings route groups.
- Query key factories are normalized for touched feature hooks.
- Build route size output is captured at least once after the environment is fixed.

## 12. Design Lint Guard Rollout Ledger

This ledger prevents the follow-up lint hardening work from being lost while the
frontend is refactored task-by-task. Apply one guard at a time. After each guard:

1. Add or tighten the rule in `frontend/scripts/check-design-system.mjs`,
   `frontend/scripts/check-frontend-architecture.mjs`, or `frontend/eslint.config.mjs`.
2. Run the new guard and classify every failure as either a real design drift or
   a narrow runtime/layout exception.
3. Fix real drift before adding exceptions.
4. Keep exceptions file-specific and context-specific, with a reason.
5. Run the verification gate.
6. Commit that single guard before starting the next one.

Per-guard verification gate:

```bash
cd frontend
pnpm lint:design-system
pnpm lint
pnpm build
```

If the guard changes user-visible copy or aria labels, also run:

```bash
cd frontend
pnpm lint:i18n
```

If the guard touches layout-heavy shared surfaces, also capture a small visual
smoke set from a real dev server: dashboard, settings, one resource list, and one
chat/artifact page.

### Rollout Checklist

- [ ] **Guard 1: Arbitrary spacing and size utilities**
  - Target: `w-[...]`, `h-[...]`, `min-w-[...]`, `max-w-[...]`,
    `gap-[...]`, `p-[...]`, `m-[...]`, `grid-cols-[...]`, and similar page-local
    sizing utilities.
  - Goal: keep layout dimensions on Moldy tokens, component APIs, or narrow
    data-driven exceptions.
  - Expected exceptions: dynamic grids, resizable panels, trace/timeline bars,
    and file-tree indentation.

- [ ] **Guard 2: Semantic color utility usage**
  - Target: product-surface uses of direct palette families such as `bg-blue-*`,
    `text-purple-*`, `border-emerald-*`, and similar one-off colors.
  - Goal: use semantic tokens and Moldy classes for surfaces; keep color families
    mostly to status badges, icon dots, charts, and vendor/runtime visualizations.
  - Expected exceptions: Agent Prism category colors, usage metric bars, and
    explicitly named status/tone tokens.

- [ ] **Guard 3: z-index, fixed, and absolute positioning**
  - Target: new arbitrary z-indexes, high `z-*` utilities, and page-local
    `fixed`/`absolute` overlays.
  - Goal: prevent overlap regressions and keep stacking contexts centralized.
  - Expected exceptions: dialogs, popovers, dropdowns, chat right rail mobile
    layer, resize handles, and timeline markers.

- [ ] **Guard 4: Typography drift**
  - Target: `leading-[...]`, `tracking-[...]`, negative tracking, and page-local
    one-off typographic tweaks.
  - Goal: reduce clipping and inconsistent Korean/English text rhythm across
    compact panels, tabs, buttons, and cards.
  - Expected exceptions: code blocks, data tables, monospace trace views, and
    third-party document/artifact renderers.

- [ ] **Guard 5: Manual SVG icon drift**
  - Target: inline `<svg>` in product buttons, menus, tabs, and toolbars.
  - Goal: prefer `lucide-react` or Moldy-owned icon primitives for consistent
    stroke, size, and accessibility.
  - Expected exceptions: generated/user artifacts, third-party viewers, charts,
    logos, and icons that do not exist in the installed icon set.

- [ ] **Guard 6: Nested cards and section-as-card warning**
  - Target: `Card` inside `Card`, `moldy-card` inside `moldy-card`, and page
    sections styled as large floating cards.
  - Goal: keep cards for repeated items, dialogs, and framed tools rather than
    nested page structure.
  - Rollout mode: start as report/warning with a baseline because this rule can
    have false positives.

- [ ] **Guard 7: Accessibility lint**
  - Target: add JSX accessibility coverage for alt text, interactive handlers,
    invalid anchors, and keyboard access.
  - Goal: catch focus and keyboard regressions before visual QA.
  - Rollout mode: install/configure the plugin, baseline existing findings, then
    make new findings blocking.

- [ ] **Guard 8: Common component usage**
  - Target: route-local reimplementation of dialogs, tabs, resource panels,
    empty/loading/error states, and page headers.
  - Goal: make the refactor stick by pointing new work to `DialogShell`,
    `ResourcePage`, `ResourcePanel`, `ResourceListCard`, `CountedTabs`, shared
    list states, and settings form primitives.
  - Rollout mode: extend `check-frontend-architecture.mjs` with narrow import or
    role-based checks and an intentional allowlist.

### Current Narrow Inline Style Exceptions

Keep these exceptions narrow. They are not design choices; they are runtime,
data, or library API requirements that cannot be represented safely as static
Tailwind classes.

| File | Runtime need | Why it stays exceptional |
| --- | --- | --- |
| `frontend/src/components/skill/skill-package-tree.tsx` | `paddingLeft: depth * 12 + 4` | File-tree indentation depends on arbitrary package depth. |
| `frontend/src/components/chat/markdown-code-highlighter.tsx` | `style={oneDark}` and `customStyle` | `react-syntax-highlighter` expects a theme object and code-block style API. |
| `frontend/src/components/usage/spend-bar-chart.tsx` | `width: ${widthPct}%` | Bar width is calculated from cost/token/request data. |
| `frontend/src/components/shared/resource-layout.tsx` | `gridTemplateColumns: repeat(auto-fill, minmax(...))` | Shared `ResourceGrid` exposes a responsive minimum column width API. |
| `frontend/src/components/agent-prism/SpanCard/SpanCard.tsx` | dynamic `gridTemplateColumns` and content width | Trace tree connector and content columns depend on nesting depth and expand-button placement. |
| `frontend/src/components/agent-prism/SpanCard/SpanCardTimeline.tsx` | dynamic `left` and `width` percentages | Timeline bar position depends on span start/end time. |
| `frontend/src/components/chat/tool-ui/phase-timeline-ui.tsx` | `--phase-ratio` CSS variable | Phase progress depends on completed todo ratio. |
| `frontend/src/components/chat/right-rail/chat-right-rail.tsx` | `--chat-right-rail-width` and width | Chat right rail width is user-resizable and viewport-clamped. |

Exception rule of thumb: allow runtime numbers, data percentages, third-party
component style APIs, and CSS variables that drive reusable components. Do not
allow one-off visual decisions such as color, spacing, radius, shadow,
typography, or stacking unless the rule above proves the value is runtime-driven.

## 13. Non-Visual Lint Guard Rollout Ledger

This ledger tracks lint/preflight hardening that is not primarily visual. Current
repo inspection showed these useful signals:

- `frontend/eslint.config.mjs` is thin: Next core web vitals + TypeScript +
  Prettier, with `e2e/**` ignored.
- `frontend/scripts/check-frontend-architecture.mjs` already reports direct
  `DialogContent`, raw tablists, page-level Client Components, and raw query
  keys, with a strict baseline.
- Source scan on 2026-06-17 found: `as any` 0, `@ts-ignore`/`@ts-expect-error`
  0, rough non-null assertions 26, `console.*` 26, raw `fetch(` 7,
  storage access 22, `dangerouslySetInnerHTML` 1, raw `queryKey` arrays 76,
  raw `invalidateQueries` arrays 39, direct `@/lib/api/*` imports from
  `app/components` 30, `test.only` 0, `test.skip` 38, `page.waitForTimeout` 2.

Apply these one by one, with a baseline for existing findings and blocking rules
for new findings.

### Non-Visual Rollout Checklist

- [x] **Guard 1: Type-safety suppressions**
  - Target: keep `as any`, `@ts-ignore`, and `@ts-expect-error` at zero; report
    non-null assertions (`!`) outside narrow DOM/ref/test contexts.
  - Why: the project already has zero `as any` and zero TypeScript suppression
    comments, so this can become a zero-tolerance rule immediately.
  - Tooling: ESLint rules plus a small custom scan for suppression comments.
  - Implemented: `pnpm lint:type-safety` scans `src`, `e2e`, and `tests` with
    TypeScript AST checks for explicit `any`, TS suppression comments, and
    postfix non-null assertions. ESLint also blocks non-null assertions in
    normal `pnpm lint`.

- [x] **Guard 2: TanStack Query key factories**
  - Target: raw `queryKey: [...]` and raw `invalidateQueries({ queryKey: [...] })`
    outside key factory files.
  - Why: there are many raw query key arrays, which makes cache invalidation and
    stale UI bugs easier to introduce during commonization.
  - Tooling: extend `check-frontend-architecture.mjs` strict baseline, then move
    feature by feature to `src/lib/query-keys` or feature-local key factories.
  - Implemented: raw `queryKey: [...]` usage in `src` product code was migrated
    to feature query key factories. `check-frontend-architecture.mjs` no longer
    baselines raw query key files and now blocks direct raw `queryKey` arrays
    even in files that define their own key factories.

- [x] **Guard 3: API boundary and raw fetch**
  - Target: `fetch(` in `src/app` and `src/components`, plus direct
    `@/lib/api/*` imports from deeply interactive components where a domain hook
    should own query/mutation behavior.
  - Why: API calls should centralize credentials, CSRF, error normalization,
    retry behavior, and response typing.
  - Expected exceptions: SSE transport, artifact binary loading, download URLs,
    and other low-level `src/lib/api` or `src/lib/sse` modules.
  - Implemented: product-surface raw `fetch(` in `src/app` and
    `src/components` is now blocked by `check-frontend-architecture.mjs`.
    Existing fetches were moved into `lib/api`, `lib/sse`, or the auth-page
    session helper. Direct domain API imports from product surfaces are tracked
    in the strict baseline so new imports fail while existing feature-specific
    migrations continue incrementally.

- [x] **Guard 4: Browser storage and auth-sensitive state**
  - Target: `localStorage`/`sessionStorage` in product components.
  - Why: Moldy auth uses HttpOnly cookies and CSRF; storage should not become a
    place for tokens, secrets, credentials, or long-lived sensitive state.
  - Expected exceptions: UI preferences, temporary route handoff state, and
    explicitly named Jotai persistence helpers.
  - Implemented: product-surface `localStorage`, `sessionStorage`, and
    `document.cookie` access in `src/app` and `src/components` is now blocked by
    `check-frontend-architecture.mjs`. Existing route handoff, onboarding flags,
    and sidebar cookie writes were moved behind explicit `src/lib` helpers.

- [x] **Guard 5: Console/logging policy**
  - Target: `console.log`, `console.debug`, `console.info` in `src`, and
    uncontrolled `console.warn/error` scattered across product code.
  - Why: console noise hides real runtime regressions, and error reporting should
    be intentional.
  - Rollout mode: create or adopt a small logger/reporting helper; allow
    `console.warn/error` only in approved low-level catch blocks until migrated.
  - Implemented: direct `console.log/debug/info/warn/error` in `src` is now
    blocked by `check-frontend-architecture.mjs`; existing warning/error call
    sites were migrated to `src/lib/logging/client-logger.ts`.

- [x] **Guard 6: Unsafe HTML / injection sinks**
  - Target: `dangerouslySetInnerHTML`, `innerHTML`, `DOMParser`, and URL-opening
    helpers that accept untrusted content.
  - Why: chat, artifacts, markdown, document preview, and marketplace content can
    all carry user-generated data.
  - Expected exceptions: audited markdown/artifact renderers with sanitization or
    library-owned rendering contracts.
  - Implemented: unsafe HTML/SVG sinks, direct `window.open`, and `_blank`
    links without `noopener noreferrer` are blocked by
    `check-frontend-architecture.mjs`. Docx clearing now uses
    `replaceChildren()`, URL opening is centralized in
    `src/lib/browser/window-open.ts`, and Mermaid SVG rendering is the sole
    audited `dangerouslySetInnerHTML` allowlist entry.

- [x] **Guard 7: E2E lint and test hygiene**
  - Target: include `e2e/**` in a separate lint command or test-hygiene script.
    Block `test.only`, unreviewed `test.skip`, `page.waitForTimeout`, and
    `force: true` interactions.
  - Why: E2E is currently ignored by ESLint, yet it is the main confidence layer
    for chat/runtime regressions.
  - Rollout mode: allow documented backend/runtime skips, but require a reason
    string and preferably a linked condition.
  - Implemented: `pnpm lint:e2e-hygiene` scans `e2e` and `tests` with the
    TypeScript AST, blocks `test.only` / `describe.only`, fixed
    `waitForTimeout`, `force: true`, and skips without an explicit condition
    reason. Existing visual capture waits now use a paint-based helper, and the
    remaining seeded-card skip has a reason string.

- [x] **Guard 8: Import boundaries and private folders**
  - Target: imports from another route's `_components`, `_hooks`, or `_lib`;
    domain hooks imported into `src/components/ui`; and new barrel `index.ts`
    files.
  - Why: this keeps the folder architecture from drifting back after the refactor.
  - Tooling: extend `check-frontend-architecture.mjs`; consider
    `eslint-plugin-boundaries` only after the custom rules stabilize.
  - Implemented: `check-frontend-architecture.mjs` now resolves relative and
    `@/` imports and blocks cross-route private folder imports, domain hook
    imports inside `components/ui`, and new barrel `index.ts(x)` files.
    `SubAgentsDialog` and audit event content were moved out of sibling route
    private folders. The two `agents/new/manual` imports from the agent settings
    route are intentionally baselined until the larger agent settings feature
    module extraction in the refactor plan.

- [x] **Guard 9: Heavy dependency and client-boundary imports**
  - Target: Mermaid, document viewers, spreadsheet/PPT/HWP libraries, syntax
    highlighters, and trace/debug viewers imported into route/page shells instead
    of lazy Client islands.
  - Why: page-level Client Components and direct heavy imports are the most likely
    bundle/performance regressions during commonization.
  - Tooling: custom import scan first; later pair with bundle analyzer budgets.
  - Implemented: `check-frontend-architecture.mjs` now blocks heavy client
    dependency imports outside approved lazy islands/providers and blocks route
    shells from statically importing heavy viewer/island components. The trace
    debugger page now lazy-loads `TraceDebuggerView`.

- [x] **Guard 10: Date/number formatting and timezone drift**
  - Target: ad hoc `new Date(...).toLocaleString()`, `toLocaleDateString()`, and
    one-off number formatting in `src/app` and `src/components`.
  - Why: product formatting should consistently handle locale, timezone, and
    empty/invalid values.
  - Expected exceptions: low-level format helpers and tests.
  - Implemented: `src/lib/utils/display-format.ts` centralizes product
    date/time, number, USD, compact count, and byte formatting with explicit
    fallback and KST defaults. `check-frontend-architecture.mjs` now blocks
    direct `toLocale*` and `new Intl.*Format` usage in `src/app` and
    `src/components`.

Recommended rollout order:

1. Type-safety suppressions, because current count is already zero.
2. E2E test hygiene, because it is currently outside ESLint.
3. Query key factories, because it directly prevents stale UI and cache bugs.
4. API boundary / raw fetch.
5. Browser storage and auth-sensitive state.
6. Console/logging policy.
7. Unsafe HTML / injection sinks.
8. Import boundaries and private folders.
9. Heavy dependency/client-boundary imports.
10. Date/number formatting.

## 14. Future Items

These are intentionally not part of the first modernization pass:

1. **Chat runtime decomposition**
   - Split `src/lib/chat/use-chat-runtime.ts` into stream lifecycle, message projection, cache synchronization, usage handling, HITL decision handling, and adapter wiring.
   - Requires existing chat runtime tests plus `chat-langgraph-v3` E2E.

2. **Assistant thread decomposition**
   - Split `src/components/chat/assistant-thread.tsx` into thread shell, message parts, tool/data UI registry rendering, artifact controls, and composer area.
   - Preserve existing lazy builder overrides.

3. **Chat/message virtualization**
   - Measure long conversations first.
   - Candidate only after thread decomposition.

4. **Trace/debugger large data optimization**
   - `src/components/chat/trace-debugger-view.tsx` has many map/filter/sort operations and detail queries.
   - Add virtualization or lazy detail expansion after route/commonization work.

5. **Bundle analyzer budget**
   - Add `@next/bundle-analyzer` or another analyzer only after Node/dependency preflight is stable.
   - Establish route JS budgets from real build output.

6. **Unused dependency cleanup**
   - `chart.js` appears unused in source during discovery.
   - Confirm with depcheck and bundle analyzer before removal.

7. **Type barrel split**
   - `src/lib/types/index.ts` is 739 lines and imported from many places.
   - Most imports are type-only, so this is maintainability more than immediate bundle risk.
   - Split into domain imports as feature modules stabilize.

8. **Full feature-directory migration**
   - After MCP/Tools/Skills/Schedules, continue with Marketplace, Models, Credentials, Agent settings, and Artifacts.

9. **RSC data prefetch/hydration**
   - Consider Server Component loaders plus TanStack hydration for list/detail pages.
   - Do this after route-local Client Components make data boundaries clear.

10. **Image policy audit**
    - Keep raw `<img>` for user-generated or blob/preview content where `next/image` is inappropriate.
    - Use `next/image` for app-owned logos, avatars, and static assets.
    - Add an AGENTS rule only after classifying current exceptions.

11. **Accessibility audit**
    - Run keyboard/focus checks on sidebar, tabs, resource cards, dialogs, and wizard flows.

12. **LangGraph v3 visual matrix stability**
    - Target `frontend/e2e/chat-langgraph-v3-visual-matrix.spec.ts` separately from
      lint-guard rollout.
    - A focused `active streaming` run timed out waiting for `fixture complete.`
      after the stream started; smoke passes with explicit legacy runtime and
      rate-limit-disabled E2E env.
    - Investigate scripted runtime completion, stale E2E data cleanup, and
      LangGraph v3 stream visibility before treating the visual matrix as a
      mandatory per-guard gate.
    - Replace remaining raw tablist/radiogroup/button-card patterns with shared accessible primitives.

12. **Strict no-restricted-imports ESLint rules**
    - After architecture guard stabilizes, move some checks into `eslint.config.mjs`.
    - Candidates: direct `@/components/ui/dialog` imports, raw resource `Card`, route page `'use client'`.

## 15. Self-Review Checklist

- Spec coverage:
  - Folder structure: Tasks 5, 6, 7, 12.
  - Commonization: Tasks 3, 4, 6, 8.
  - Performance/preflight: Tasks 1, 2, 10, 13.
  - Lint/AGENTS/CLAUDE considerations: Task 12.
  - Future items: Section 14.
- Placeholder scan:
  - No implementation step uses placeholder markers as an action.
  - Future work is explicitly separated in Section 14.
- Type/path consistency:
  - New shared component paths are under `frontend/src/components/shared`.
  - Route-local MCP/Tools/Skills targets are under their route `_components`.
  - `CLAUDE.md` is not duplicated because it already references `@AGENTS.md`.
