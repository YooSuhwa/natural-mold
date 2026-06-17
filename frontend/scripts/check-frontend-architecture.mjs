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
