#!/usr/bin/env node

import { readdir, readFile } from 'node:fs/promises'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

const SCANNED_EXTENSIONS = new Set(['.css', '.ts', '.tsx'])

const SKIP_PATH_PARTS = new Set([
  '.next',
  'coverage',
  'e2e',
  'messages',
  'node_modules',
  'test-results',
  'tests',
])

const SKIP_FILE_PATTERNS = [
  /\.test\.[cm]?[tj]sx?$/,
  /\.spec\.[cm]?[tj]sx?$/,
  /(^|\/)src\/components\/ui\//,
]

const ZERO_TOLERANCE_RULES = [
  {
    id: 'large-radius-utility',
    pattern: /\brounded-(?:xl|2xl|3xl)\b/g,
    message: 'use a Moldy surface/radius class instead of page-local large radius utilities',
  },
  {
    id: 'shadow-utility',
    pattern: /\bshadow-(?:sm|md|lg|xl|2xl)\b|shadow-\[[^\]]+\]/g,
    message: 'use a Moldy elevation class/token instead of page-local shadow utilities',
  },
  {
    id: 'raw-hex-utility',
    pattern: /\b(?:bg|text|border|from|via|to|ring|fill|stroke)-\[#/g,
    message: 'map raw hex color utilities into Moldy semantic tokens',
  },
  {
    id: 'direct-palette-utility',
    pattern:
      /\b(?:bg|text|border|ring|fill|stroke|from|via|to)-(?:slate|gray|zinc|neutral|stone|red|orange|amber|yellow|lime|green|emerald|teal|cyan|sky|blue|indigo|violet|purple|fuchsia|pink|rose)-[0-9]{2,3}(?:\/[0-9]{1,3})?\b/g,
    message: 'use Moldy semantic color tokens/status classes instead of direct palette utilities',
  },
  {
    id: 'resource-tone-background',
    pattern: /\bbg-\[var\(--moldy-(?:mint|sky|violet|amber|rose)\)\]/g,
    message:
      'use neutral resource cards; keep tone colors to icons, dots, status signals, and interaction states',
  },
  {
    id: 'arbitrary-spacing-utility',
    pattern: /!?\b-?(?:gap|p|px|py|pt|pr|pb|pl|m|mx|my|mt|mr|mb|ml)-\[[^\]]+\]/g,
    message: 'use Tailwind spacing scale tokens or a Moldy component spacing API',
  },
  {
    id: 'arbitrary-icon-size',
    pattern: /!?\bsize-\[[^\]]+\]/g,
    message: 'use Tailwind size scale tokens or a shared icon/button size primitive',
  },
  {
    id: 'arbitrary-text-class',
    pattern: /\btext-\[[^\]]+\]/g,
    message: 'use Moldy typography classes or Tailwind text scale tokens',
  },
  {
    id: 'outline-none',
    pattern: /\boutline-none\b/g,
    message: 'use outline-hidden plus focus-visible replacement, or a Moldy focus class',
  },
  {
    id: 'transition-all',
    pattern: /\btransition-all\b/g,
    message: 'list transition properties explicitly',
  },
]

const ARBITRARY_LAYOUT_ALLOWLIST = [
  {
    filePath: 'src/app/dashboard-page-client.tsx',
    tokenPattern: /^grid-cols-\[1\.4fr_1fr\]$/,
    reason: 'dashboard primary/secondary metric balance',
  },
  {
    filePath: 'src/app/agents/[agentId]/conversations/[conversationId]/traces/page.tsx',
    tokenPattern: /^h-\[calc\(100vh-12rem\)\]$/,
    reason: 'trace route loading skeleton fills remaining viewport height',
  },
  {
    filePath: 'src/app/agents/[agentId]/settings/page.tsx',
    tokenPattern: /^min-h-\[420px\]$/,
    reason: 'settings form route keeps a stable editor shell floor',
  },
  {
    filePath: 'src/app/agents/[agentId]/visual-settings/page.tsx',
    tokenPattern: /^h-\[calc\(100vh-10rem\)\]$/,
    reason: 'visual builder loading skeleton fills remaining viewport height',
  },
  {
    filePath: 'src/app/agents/new/manual/page.tsx',
    tokenPattern: /^(?:h-\[calc\(100vh-10rem\)\]|min-h-\[420px\])$/,
    reason: 'manual builder route keeps a stable editor shell floor',
  },
  {
    filePath: 'src/app/marketplace/[item-id]/page.tsx',
    tokenPattern: /^grid-cols-\[120px_1fr\]$/,
    reason: 'marketplace metadata label/value grid',
  },
  {
    filePath: 'src/app/settings/_components/audit-events-content.tsx',
    tokenPattern:
      /^grid-cols-\[(?:minmax\(0,1fr\)_180px_150px|minmax\(0,1fr\)_340px|170px_minmax\(0,1fr\)_160px|96px_minmax\(0,1fr\))\]$/,
    reason: 'audit event rows use table-like responsive columns',
  },
  {
    filePath: 'src/app/settings/loading.tsx',
    tokenPattern: /^grid-cols-\[minmax\(0,1fr\)_280px\]$/,
    reason: 'settings skeleton mirrors the settings navigation rail layout',
  },
  {
    filePath: 'src/app/settings/page.tsx',
    tokenPattern: /^grid-cols-\[minmax\(0,1fr\)_280px\]$/,
    reason: 'settings layout keeps a fixed secondary navigation rail',
  },
  {
    filePath: 'src/app/settings/usage/page.tsx',
    tokenPattern: /^max-h-\[420px\]$/,
    reason: 'usage table scroll viewport',
  },
  {
    filePath: 'src/app/shared/[shareId]/page.tsx',
    tokenPattern: /^max-w-\[85%\]$/,
    reason: 'shared transcript bubble width ratio',
  },
  {
    filePath: 'src/components/agent-prism/SpanCard/SpanCardConnector.tsx',
    tokenPattern: /^h-\[7px\]$/,
    reason: 'trace connector pixel alignment',
  },
  {
    filePath: 'src/components/agent-prism/TraceViewer/TraceViewer.tsx',
    tokenPattern: /^h-\[calc\(100vh-50px\)\]$/,
    reason: 'trace viewer fills viewport below its toolbar',
  },
  {
    filePath: 'src/components/agent/sub-agents-dialog.tsx',
    tokenPattern: /^(?:h|max-h)-\[60vh\]$/,
    reason: 'dual-column picker uses viewport-bound scroll regions',
  },
  {
    filePath: 'src/components/agent/visual-settings/dialogs/middlewares-dialog.tsx',
    tokenPattern: /^(?:h|max-h)-\[60vh\]$/,
    reason: 'visual builder picker uses viewport-bound scroll regions',
  },
  {
    filePath: 'src/components/agent/visual-settings/dialogs/tools-skills-current-column.tsx',
    tokenPattern: /^(?:h|max-h)-\[60vh\]$/,
    reason: 'visual builder current-item column uses viewport-bound scroll regions',
  },
  {
    filePath: 'src/components/agent/visual-settings/nodes/agent-node.tsx',
    tokenPattern: /^h-\[600px\]$/,
    reason: 'React Flow agent detail pane needs a stable inspection height',
  },
  {
    filePath: 'src/components/artifacts/artifact-library-content.tsx',
    tokenPattern:
      /^(?:grid-cols-\[minmax\(360px,0\.95fr\)_minmax\(420px,1\.05fr\)\]|min-h-\[420px\]|max-h-\[(?:640|680)px\])$/,
    reason: 'artifact library master/detail panes need bounded inspection areas',
  },
  {
    filePath: 'src/components/artifacts/artifact-library-filters.tsx',
    tokenPattern: /^grid-cols-\[minmax\(220px,1fr\)_160px_180px_150px_150px\]$/,
    reason: 'artifact filter controls keep scan-friendly column widths',
  },
  {
    filePath: 'src/components/auth/UserMenu.tsx',
    tokenPattern: /^w-\[--anchor-width\]$/,
    reason: 'dropdown content follows the trigger width CSS variable',
  },
  {
    filePath: 'src/components/chat/assistant-thread.tsx',
    tokenPattern: /^max-w-\[80%\]$/,
    reason: 'chat message bubbles use a transcript column ratio',
  },
  {
    filePath: 'src/components/chat/builder-overrides.tsx',
    tokenPattern: /^max-w-\[72%\]$/,
    reason: 'builder override bubbles use a narrower transcript ratio',
  },
  {
    filePath: 'src/components/chat/chat-image.tsx',
    tokenPattern:
      /^(?:(?:h|max-h)-\[calc\(100vh-2rem\)\]|(?:w|max-w)-\[calc\(100vw-2rem\)\]|w-\[min\(calc\(100vw-2rem\),1200px\)\])$/,
    reason: 'fullscreen image preview is viewport-clamped',
  },
  {
    filePattern: /^src\/components\/chat\/artifacts\/providers\/.+\.tsx$/,
    tokenPattern: /^(?:(?:h|max-h)-\[(?:520|620)px\]|w-\[(?:125|150)%\])$/,
    reason: 'artifact previews use bounded inspection panes and zoom widths',
  },
  {
    filePath: 'src/components/chat/right-rail/tool-result-panel-content.tsx',
    tokenPattern: /^max-h-\[60vh\]$/,
    reason: 'tool output preview is viewport-clamped',
  },
  {
    filePath: 'src/components/chat/tool-ui/memory-tool-ui.tsx',
    tokenPattern: /^max-w-\[min\(34rem,54vw\)\]$/,
    reason: 'memory item content truncates at a responsive formula',
  },
  {
    filePath: 'src/components/chat/trace-debugger-view.tsx',
    tokenPattern: /^grid-cols-\[280px_minmax\(420px,1fr\)_minmax\(360px,42%\)\]$/,
    reason: 'trace debugger uses a fixed three-pane inspection grid',
  },
  {
    filePath: 'src/components/credential/credential-create-modal.tsx',
    tokenPattern: /^max-h-\[40vh\]$/,
    reason: 'credential field list is viewport-clamped inside a modal',
  },
  {
    filePath: 'src/features/schedules/components/schedule-form.tsx',
    tokenPattern: /^grid-cols-\[minmax\(0,0\.9fr\)_minmax\(0,1\.1fr\)\]$/,
    reason: 'schedule form keeps primary settings and review panels balanced',
  },
  {
    filePath: 'src/lib/design-tokens.ts',
    tokenPattern:
      /^(?:w-\[(?:400|560|720|920|1080)px\]|h-\[(?:480|640|760)px\]|max-h-\[calc\(100vh-4rem\)\])$/,
    reason: 'dialog dimensions are centralized design tokens consumed by DialogShell',
  },
  {
    filePath: 'src/components/model/model-discover-panel.tsx',
    tokenPattern: /^max-h-\[44vh\]$/,
    reason: 'model discovery list is viewport-clamped',
  },
  {
    filePath: 'src/components/shared/dialog-shell.tsx',
    tokenPattern: /^max-w-\[calc\(100%-2rem\)\]$/,
    reason: 'mobile dialogs keep a calculated viewport gutter',
  },
  {
    filePath: 'src/components/skill/skill-detail-text-editor.tsx',
    tokenPattern: /^min-h-\[400px\]$/,
    reason: 'text skill editor keeps a minimum writing area',
  },
  {
    filePath: 'src/components/skill/skill-evaluation-tab.tsx',
    tokenPattern: /^grid-cols-\[minmax\(0,0\.9fr\)_minmax\(20rem,1fr\)\]$/,
    reason: 'skill evaluation keeps form/result split panes',
  },
  {
    filePath: 'src/components/skill/skill-file-editor-pane.tsx',
    tokenPattern: /^max-h-\[420px\]$/,
    reason: 'package image preview is capped inside the editor pane',
  },
  {
    filePath: 'src/components/skill/skill-history-tab.tsx',
    tokenPattern: /^grid-cols-\[minmax\(0,1fr\)_minmax\(18rem,0\.85fr\)\]$/,
    reason: 'skill history keeps version/detail split panes',
  },
]

const POSITIONING_UTILITY_ALLOWLIST = [
  {
    filePath: 'src/app/shared/[shareId]/page.tsx',
    rulePattern: /^high-z-index-utility$/,
    context: /sticky top-0 z-40/,
    reason: 'shared transcript header must stay above the scrollable conversation',
  },
  {
    filePath: 'src/features/schedules/components/schedule-form.tsx',
    rulePattern: /^absolute-overlay-utility$/,
    context: /moldy-popover absolute left-0 right-0 top-full z-20/,
    reason: 'schedule form timezone picker is an anchored popover',
  },
  {
    filePath: 'src/app/agents/[agentId]/settings/_components/right-panel/settings-panel.tsx',
    rulePattern: /^absolute-overlay-utility$/,
    context: /absolute inset-0 flex items-center justify-center/,
    reason: 'right-panel save overlay is scoped to its rounded button',
  },
  {
    filePath: 'src/components/agent-prism/DetailsView/DetailsViewContentViewer.tsx',
    rulePattern: /^absolute-overlay-utility$/,
    context: /absolute right-1\.5 top-1\.5 z-10/,
    reason: 'Agent Prism raw data copy action floats inside the code viewer',
  },
  {
    filePattern: /^src\/components\/agent-prism\/SpanCard\/.+\.tsx$/,
    rulePattern: /^absolute-overlay-utility$/,
    context: /absolute/,
    reason: 'Agent Prism trace connectors and timeline markers require absolute positioning',
  },
  {
    filePath: 'src/components/chat/chat-image.tsx',
    rulePattern: /^absolute-overlay-utility$/,
    context: /!loaded && 'absolute inset-0 opacity-0'/,
    reason: 'image preview keeps unloaded image out of layout flow',
  },
  {
    filePath: 'src/components/chat/right-rail/chat-right-rail.tsx',
    rulePattern: /^(?:absolute-overlay-utility|fixed-overlay-utility|high-z-index-utility)$/,
    context:
      /(?:fixed inset-0 z-40|absolute inset-0|absolute inset-y-0 left-0 z-20|moldy-side-panel moldy-right-rail-mobile absolute inset-y-0 right-0)/,
    reason: 'chat right rail owns its resize handle and mobile modal layer',
  },
]

const TYPOGRAPHY_UTILITY_ALLOWLIST = [
  {
    filePath: 'src/components/agent-prism/SpanCard/SpanCard.tsx',
    rulePattern: /^arbitrary-typography-utility$/,
    context: /text-agentprism-foreground max-w-32 truncate text-sm leading-\[14px\]/,
    reason: 'Agent Prism trace title line-height is tied to compact span-card geometry',
  },
]

const STYLE_ATTRIBUTE_ALLOWLIST = [
  {
    filePath: 'src/components/skill/skill-package-tree.tsx',
    reason: 'tree depth indentation',
    context: /style=\{\{\s*paddingLeft:\s*depth \* 12 \+ 4\s*\}\}/,
  },
  {
    filePath: 'src/components/chat/markdown-code-highlighter.tsx',
    reason: 'react-syntax-highlighter theme object',
    context: /style=\{oneDark\}/,
  },
  {
    filePath: 'src/components/usage/spend-bar-chart.tsx',
    reason: 'data-driven bar width',
    context: /style=\{\{\s*width:\s*`\$\{widthPct\}%`\s*\}\}/,
  },
  {
    filePath: 'src/components/shared/resource-layout.tsx',
    reason: 'resource grid column width API',
    context: /style=\{\{[\s\S]{0,180}gridTemplateColumns:/,
  },
  {
    filePath: 'src/components/agent-prism/SpanCard/SpanCard.tsx',
    reason: 'Agent Prism trace grid layout',
    context: /style=\{\{[\s\S]{0,240}gridTemplateColumns/,
  },
  {
    filePath: 'src/components/agent-prism/SpanCard/SpanCard.tsx',
    reason: 'Agent Prism trace content width',
    context: /style=\{\{[\s\S]{0,240}contentWidth/,
  },
  {
    filePath: 'src/components/agent-prism/SpanCard/SpanCardTimeline.tsx',
    reason: 'Agent Prism timeline percentages',
    context: /style=\{\{[\s\S]{0,260}startPercent[\s\S]{0,260}widthPercent/,
  },
  {
    filePath: 'src/components/chat/tool-ui/phase-timeline-ui.tsx',
    reason: 'data-driven phase progress ratio',
    context: /style=\{phaseStyle\}/,
  },
  {
    filePath: 'src/components/chat/right-rail/chat-right-rail.tsx',
    reason: 'dynamic chat right rail width CSS variables',
    context: /style=\{rightRailStyle\}/,
  },
]

function normalizePath(filePath) {
  return filePath.split(path.sep).join('/')
}

function shouldScanPath(filePath) {
  const normalized = normalizePath(filePath)
  const parts = normalized.split('/')
  if (parts.some((part) => SKIP_PATH_PARTS.has(part))) return false
  if (!SCANNED_EXTENSIONS.has(path.extname(normalized))) return false
  if (SKIP_FILE_PATTERNS.some((pattern) => pattern.test(normalized))) return false
  return true
}

function lineNumberAt(source, index) {
  let line = 1
  for (let i = 0; i < index; i += 1) {
    if (source.charCodeAt(i) === 10) line += 1
  }
  return line
}

async function collectFiles(rootDir) {
  const out = []

  async function walk(dir) {
    const entries = await readdir(dir, { withFileTypes: true })
    for (const entry of entries) {
      const fullPath = path.join(dir, entry.name)
      if (entry.isDirectory()) {
        if (!SKIP_PATH_PARTS.has(entry.name)) await walk(fullPath)
        continue
      }
      if (shouldScanPath(fullPath)) out.push(fullPath)
    }
  }

  await walk(rootDir)
  return out
}

function compactSnippet(source, index) {
  return source
    .slice(index, index + 160)
    .replace(/\s+/g, ' ')
    .trim()
}

function isAllowedStyleAttribute(filePath, source, index) {
  const context = source.slice(index, index + 520)
  return STYLE_ATTRIBUTE_ALLOWLIST.some(
    (entry) => entry.filePath === filePath && entry.context.test(context),
  )
}

function findZeroToleranceIssues(source, filePath) {
  const issues = []
  for (const rule of ZERO_TOLERANCE_RULES) {
    for (const match of source.matchAll(rule.pattern)) {
      issues.push({
        filePath,
        line: lineNumberAt(source, match.index ?? 0),
        rule: rule.id,
        message: rule.message,
        snippet: compactSnippet(source, match.index ?? 0),
      })
    }
  }
  return issues
}

function findInlineStyleIssues(source, filePath) {
  const issues = []
  const styleAttributeRe = /\bstyle\s*=\s*\{/g

  for (const match of source.matchAll(styleAttributeRe)) {
    const index = match.index ?? 0
    if (isAllowedStyleAttribute(filePath, source, index)) continue
    issues.push({
      filePath,
      line: lineNumberAt(source, index),
      rule: 'inline-style',
      message: 'move visual styling into Moldy classes, or add a narrow allowlist entry',
      snippet: compactSnippet(source, index),
    })
  }

  return issues
}

function isAllowedArbitraryLayoutUtility(filePath, token) {
  const normalizedToken = token.replace(/^!/, '')

  return ARBITRARY_LAYOUT_ALLOWLIST.some((entry) => {
    const pathMatches =
      'filePath' in entry ? entry.filePath === filePath : entry.filePattern.test(filePath)
    return pathMatches && entry.tokenPattern.test(normalizedToken)
  })
}

function findArbitraryLayoutIssues(source, filePath) {
  const issues = []
  const layoutUtilityRe = /!?\b(?:w|h|min-w|min-h|max-w|max-h|grid-cols|grid-rows)-\[[^\]]+\]/g

  for (const match of source.matchAll(layoutUtilityRe)) {
    const index = match.index ?? 0
    const token = match[0]
    if (isAllowedArbitraryLayoutUtility(filePath, token)) continue
    issues.push({
      filePath,
      line: lineNumberAt(source, index),
      rule: 'arbitrary-layout-utility',
      message: 'use Tailwind scale tokens, a Moldy layout API, or a documented narrow exception',
      snippet: compactSnippet(source, index),
    })
  }

  return issues
}

function isAllowedPositioningUtility(filePath, rule, source, index) {
  const context = source.slice(Math.max(0, index - 180), index + 260)

  return POSITIONING_UTILITY_ALLOWLIST.some((entry) => {
    const pathMatches =
      'filePath' in entry ? entry.filePath === filePath : entry.filePattern.test(filePath)
    return pathMatches && entry.rulePattern.test(rule) && entry.context.test(context)
  })
}

function findPositioningIssues(source, filePath) {
  if (path.extname(filePath) === '.css') return []

  const issues = []
  const rules = [
    {
      id: 'high-z-index-utility',
      pattern: /\bz-\[[^\]]+\]|\bz-(?:40|50|[6-9][0-9]|[1-9][0-9]{2,})\b/g,
      message: 'use a documented overlay/stacking primitive instead of high or arbitrary z-index',
    },
    {
      id: 'fixed-overlay-utility',
      pattern: /\bfixed\b(?=[^'"`<>]{0,120}\binset-)/g,
      message: 'keep fixed viewport layers in shared overlay primitives or documented exceptions',
    },
    {
      id: 'absolute-overlay-utility',
      pattern: /\babsolute\b(?=[^'"`<>]{0,140}\b(?:inset-0|z-\d+|z-\[[^\]]+\]))/g,
      message:
        'keep absolute overlays/stacking contexts in shared primitives or documented exceptions',
    },
  ]

  for (const rule of rules) {
    for (const match of source.matchAll(rule.pattern)) {
      const index = match.index ?? 0
      if (isAllowedPositioningUtility(filePath, rule.id, source, index)) continue
      issues.push({
        filePath,
        line: lineNumberAt(source, index),
        rule: rule.id,
        message: rule.message,
        snippet: compactSnippet(source, index),
      })
    }
  }

  return issues
}

function isAllowedTypographyUtility(filePath, rule, source, index) {
  const context = source.slice(Math.max(0, index - 160), index + 220)

  return TYPOGRAPHY_UTILITY_ALLOWLIST.some(
    (entry) =>
      entry.filePath === filePath && entry.rulePattern.test(rule) && entry.context.test(context),
  )
}

function findTypographyIssues(source, filePath) {
  const issues = []
  const rules = [
    {
      id: 'arbitrary-typography-utility',
      pattern: /\b(?:leading|tracking|font)-\[[^\]]+\]/g,
      message: 'use Moldy typography classes or a documented renderer/layout exception',
    },
    {
      id: 'negative-tracking-utility',
      pattern: /\btracking-(?:tight|tighter)\b/g,
      message: 'keep letter spacing at zero unless a semantic typography class owns it',
    },
  ]

  for (const rule of rules) {
    for (const match of source.matchAll(rule.pattern)) {
      const index = match.index ?? 0
      if (isAllowedTypographyUtility(filePath, rule.id, source, index)) continue
      issues.push({
        filePath,
        line: lineNumberAt(source, index),
        rule: rule.id,
        message: rule.message,
        snippet: compactSnippet(source, index),
      })
    }
  }

  return issues
}

async function findDesignSystemIssues(rootDir = path.join(process.cwd(), 'src')) {
  const files = await collectFiles(rootDir)
  const issues = []

  for (const file of files) {
    const source = await readFile(file, 'utf8')
    const filePath = normalizePath(path.relative(process.cwd(), file))
    issues.push(...findZeroToleranceIssues(source, filePath))
    issues.push(...findArbitraryLayoutIssues(source, filePath))
    issues.push(...findPositioningIssues(source, filePath))
    issues.push(...findTypographyIssues(source, filePath))
    issues.push(...findInlineStyleIssues(source, filePath))
  }

  return issues.sort((left, right) =>
    left.filePath === right.filePath
      ? left.line - right.line
      : left.filePath.localeCompare(right.filePath),
  )
}

async function main() {
  const issues = await findDesignSystemIssues()

  if (issues.length === 0) {
    console.log('Design system guard passed.')
    console.log(
      `Allowed inline style exceptions: ${STYLE_ATTRIBUTE_ALLOWLIST.length} documented dynamic/layout cases.`,
    )
    console.log(
      `Allowed arbitrary layout exceptions: ${ARBITRARY_LAYOUT_ALLOWLIST.length} documented runtime/layout cases.`,
    )
    console.log(
      `Allowed positioning exceptions: ${POSITIONING_UTILITY_ALLOWLIST.length} documented overlay/stacking cases.`,
    )
    console.log(
      `Allowed typography exceptions: ${TYPOGRAPHY_UTILITY_ALLOWLIST.length} documented renderer/layout cases.`,
    )
    return
  }

  console.error(`Found ${issues.length} design system issue(s):`)
  for (const issue of issues) {
    console.error(
      `${issue.filePath}:${issue.line} [${issue.rule}] ${issue.message} :: ${issue.snippet}`,
    )
  }
  process.exitCode = 1
}

const currentFile = fileURLToPath(import.meta.url)
if (process.argv[1] && path.resolve(process.argv[1]) === currentFile) {
  main().catch((error) => {
    console.error(error)
    process.exitCode = 1
  })
}
