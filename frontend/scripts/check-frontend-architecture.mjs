import { readFileSync, readdirSync, statSync } from 'node:fs'
import { join, relative, sep } from 'node:path'
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

const strictBaseline = new Set([
  'tabs:src/app/(auth)/layout.tsx',
  'client-page:src/app/(auth)/login/page.tsx',
  'client-page:src/app/(auth)/register/page.tsx',
  'client-page:src/app/agents/[agentId]/conversations/[conversationId]/page.tsx',
  'client-page:src/app/agents/[agentId]/conversations/[conversationId]/traces/page.tsx',
  'client-page:src/app/agents/[agentId]/page.tsx',
  'client-page:src/app/agents/[agentId]/settings/page.tsx',
  'client-page:src/app/agents/[agentId]/visual-settings/page.tsx',
  'client-page:src/app/agents/new/conversational/page.tsx',
  'client-page:src/app/agents/new/manual/page.tsx',
  'client-page:src/app/agents/new/page.tsx',
  'client-page:src/app/agents/new/template/page.tsx',
  'client-page:src/app/marketplace/[item-id]/page.tsx',
  'client-page:src/app/marketplace/page.tsx',
  'client-page:src/app/settings/admin-audit/page.tsx',
  'client-page:src/app/settings/agent-api/page.tsx',
  'client-page:src/app/settings/credentials/page.tsx',
  'client-page:src/app/settings/marketplace-admin/page.tsx',
  'client-page:src/app/settings/memory/page.tsx',
  'client-page:src/app/settings/models/page.tsx',
  'client-page:src/app/settings/page.tsx',
  'client-page:src/app/settings/schedules/page.tsx',
  'client-page:src/app/settings/system-credentials/page.tsx',
  'client-page:src/app/settings/system-llm/page.tsx',
  'tabs:src/app/settings/usage/page.tsx',
  'client-page:src/app/settings/usage/page.tsx',
  'client-page:src/app/shared/[shareId]/page.tsx',
  'direct-api-import:src/app/agents/[agentId]/page.tsx',
  'direct-api-import:src/app/agents/new/conversational/page.tsx',
  'direct-api-import:src/app/settings/page.tsx',
  'direct-api-import:src/components/chat/artifacts/artifact-preview.tsx',
  'direct-api-import:src/components/chat/artifacts/providers/use-artifact-binary.ts',
  'direct-api-import:src/components/chat/assistant-thread.tsx',
  'direct-api-import:src/components/chat/right-rail/chat-right-rail.tsx',
  'direct-api-import:src/components/chat/trace-debugger-view.tsx',
  'direct-api-import:src/components/chat/use-conversation-row-actions.tsx',
  'direct-api-import:src/components/skill/skill-builder-dialog.tsx',
  'direct-api-import:src/components/skill/skill-detail-package-editor.tsx',
  'direct-api-import:src/components/skill/skill-file-editor-pane.tsx',
  'direct-api-import:src/components/skill/use-skill-file-remote-cache.ts',
])

function toPosixPath(path) {
  return path.split(sep).join('/')
}

function walk(dir) {
  const out = []

  for (const entry of readdirSync(dir)) {
    const full = join(dir, entry)
    const stat = statSync(full)

    if (stat.isDirectory()) {
      if (entry === 'node_modules' || entry === '.next' || entry === '__tests__') continue
      out.push(...walk(full))
      continue
    }

    if (/\.(tsx|ts)$/.test(entry)) out.push(full)
  }

  return out
}

const files = walk(srcRoot)
const issues = []

function issueKey(issue) {
  return `${issue.rule}:${issue.rel}`
}

function isProductSurface(rel) {
  return /^src\/(?:app|components)\//.test(rel)
}

function isClientLogger(rel) {
  return rel === 'src/lib/logging/client-logger.ts'
}

for (const file of files) {
  const rel = toPosixPath(relative(root, file))
  const text = readFileSync(file, 'utf8')

  if (
    text.includes("from '@/components/ui/dialog'") &&
    /\bDialog(Content)?\b/.test(text) &&
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

  if (/queryKey:\s*\[[^\]]+]/.test(text)) {
    issues.push({
      rel,
      rule: 'raw-query-key',
      message: 'Prefer feature query key factories for new TanStack Query keys.',
    })
  }

  if (isProductSurface(rel) && /\bfetch\s*\(/.test(text)) {
    issues.push({
      rel,
      rule: 'raw-fetch',
      message: 'Move raw fetch calls into lib/api, lib/sse, or a focused low-level helper.',
    })
  }

  if (isProductSurface(rel) && /\b(?:localStorage|sessionStorage)\b|document\.cookie/.test(text)) {
    issues.push({
      rel,
      rule: 'browser-storage',
      message:
        'Move browser storage and cookie access into explicit lib helpers so auth-sensitive state stays centralized.',
    })
  }

  if (!isClientLogger(rel) && /\bconsole\.(?:log|debug|info|warn|error)\b/.test(text)) {
    issues.push({
      rel,
      rule: 'direct-console',
      message: 'Use client logging helpers instead of direct console calls in src.',
    })
  }

  if (isProductSurface(rel) && /from\s+['"]@\/lib\/api\/(?!client['"])[^'"]+['"]/.test(text)) {
    issues.push({
      rel,
      rule: 'direct-api-import',
      message:
        'Route product data access through feature hooks instead of direct domain API imports.',
    })
  }
}

for (const issue of issues) {
  console.log(`${issue.rule}: ${issue.rel} - ${issue.message}`)
}

console.log(`frontend architecture issues: ${issues.length}`)

if (strict) {
  const blockingIssues = issues.filter((issue) => !strictBaseline.has(issueKey(issue)))
  console.log(`frontend architecture strict blocking issues: ${blockingIssues.length}`)
  if (blockingIssues.length > 0) exit(1)
}
