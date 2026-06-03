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

const STYLE_ATTRIBUTE_ALLOWLIST = [
  {
    filePath: 'src/components/skill/skill-package-tree.tsx',
    reason: 'tree depth indentation',
    context: /style=\{\{\s*paddingLeft:\s*depth \* 12 \+ 4\s*\}\}/,
  },
  {
    filePath: 'src/components/chat/markdown-content.tsx',
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

async function findDesignSystemIssues(rootDir = path.join(process.cwd(), 'src')) {
  const files = await collectFiles(rootDir)
  const issues = []

  for (const file of files) {
    const source = await readFile(file, 'utf8')
    const filePath = normalizePath(path.relative(process.cwd(), file))
    issues.push(...findZeroToleranceIssues(source, filePath))
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
