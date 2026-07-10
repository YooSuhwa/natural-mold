#!/usr/bin/env node

import { readdir, readFile } from 'node:fs/promises'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

const CJK_TEXT_RE = /[\p{Script=Hangul}\p{Script=Han}\p{Script=Hiragana}\p{Script=Katakana}]/u
const STRICT_USER_TEXT_RE =
  /[\p{Script=Hangul}\p{Script=Han}\p{Script=Hiragana}\p{Script=Katakana}]|[A-Za-z][A-Za-z][A-Za-z ]*[A-Za-z]/u
const SCANNED_EXTENSIONS = new Set(['.ts', '.tsx'])
const ATTRIBUTES = [
  'aria-label',
  'title',
  'description',
  'emptyTitle',
  'emptyDescription',
  'label',
  'placeholder',
  'searchPlaceholder',
  'submitLabel',
]

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
  /(^|\/)src\/components\/agent-prism\//,
  /(^|\/)src\/lib\/types\//,
  /(^|\/)src\/lib\/api\//,
  /(^|\/)src\/lib\/constants\//,
  // global-error replaces the crashed root layout, so the next-intl provider
  // is not mounted — its copy must stay static (see the file's own comment).
  /(^|\/)src\/app\/global-error\.tsx$/,
]

function normalizePath(filePath) {
  return filePath.split(path.sep).join('/')
}

export function shouldScanPath(filePath) {
  const normalized = normalizePath(filePath)
  const parts = normalized.split('/')
  if (parts.some((part) => SKIP_PATH_PARTS.has(part))) return false
  if (!SCANNED_EXTENSIONS.has(path.extname(normalized))) return false
  if (SKIP_FILE_PATTERNS.some((pattern) => pattern.test(normalized))) return false
  return true
}

function stripComments(source) {
  return source
    .replace(/\/\*[\s\S]*?\*\//g, (match) => match.replace(/[^\n]/g, ' '))
    .replace(/(^|[^:])\/\/.*$/gm, (match, prefix) => {
      const comment = match.slice(prefix.length)
      return `${prefix}${comment.replace(/[^\n]/g, ' ')}`
    })
}

function lineNumberAt(source, index) {
  let line = 1
  for (let i = 0; i < index; i += 1) {
    if (source.charCodeAt(i) === 10) line += 1
  }
  return line
}

function looksLikeCodeFragment(kind, text) {
  if (kind !== 'jsx-text') return false
  return (
    /(?:^|\s)(?:const|let|type|return|void|Promise|AsyncGenerator|Set|Record|React|Parameters|use[A-Z]\w*)\b/.test(
      text,
    ) ||
    /(?:^|\s)&\s*(?:Omit|VariantProps)\b/.test(text) ||
    /[()[\]{}]|=>|===|!==|&&|\?\?|:\s|;\s|,\s*\]/.test(text)
  )
}

function userTextReForOptions(options) {
  return options.strictAscii === false ? CJK_TEXT_RE : STRICT_USER_TEXT_RE
}

function pushIssue(issues, source, index, kind, text, filePath, options) {
  const userTextRe = userTextReForOptions(options)
  const trimmed = text.replace(/\s+/g, ' ').trim()
  if (!trimmed || !userTextRe.test(trimmed)) return
  if (looksLikeCodeFragment(kind, trimmed)) return
  issues.push({
    index,
    filePath,
    line: lineNumberAt(source, index),
    kind,
    text: trimmed,
  })
}

export function findStaticTextIssuesInSource(source, filePath = '<inline>', options = {}) {
  const scanSource = stripComments(source)
  const issues = []
  const userTextRe = userTextReForOptions(options)

  for (const attr of ATTRIBUTES) {
    const attrRe = new RegExp(
      `(?:^|[\\s<{])${attr}=(["'])([^"']*(?:${userTextRe.source})[^"']*)\\1`,
      'gu',
    )
    for (const match of scanSource.matchAll(attrRe)) {
      pushIssue(issues, scanSource, match.index ?? 0, 'jsx-attribute', match[2], filePath, options)
    }
  }

  const jsxTextRe = new RegExp(
    '(?<!=)>\\s*([^<>{}`]*(?:' + userTextRe.source + ')[^<>{}`]*)\\s*<',
    'gu',
  )
  for (const match of scanSource.matchAll(jsxTextRe)) {
    pushIssue(issues, scanSource, match.index ?? 0, 'jsx-text', match[1], filePath, options)
  }

  const toastRe = new RegExp(
    `toast\\.(?:success|error|info|warning|message)\\(\\s*(["'])([^"']*(?:${userTextRe.source})[^"']*)\\1`,
    'gu',
  )
  for (const match of scanSource.matchAll(toastRe)) {
    pushIssue(issues, scanSource, match.index ?? 0, 'toast', match[2], filePath, options)
  }

  const literalRe =
    /(?<![\w$])(["'`])((?:\\.|(?!\1)[^\n\r])*?[\p{Script=Hangul}\p{Script=Han}\p{Script=Hiragana}\p{Script=Katakana}](?:\\.|(?!\1)[^\n\r])*?)\1/gu
  for (const match of scanSource.matchAll(literalRe)) {
    if (!CJK_TEXT_RE.test(match[2])) continue
    pushIssue(issues, scanSource, match.index ?? 0, 'string-literal', match[2], filePath, options)
  }

  const sorted = issues.sort((left, right) => left.index - right.index)
  const compacted = sorted.filter((issue, issueIndex) => {
    if (issue.kind !== 'string-literal') return true
    return !sorted.some(
      (other, otherIndex) =>
        otherIndex !== issueIndex &&
        other.kind !== 'string-literal' &&
        other.line === issue.line &&
        other.text === issue.text,
    )
  })

  return compacted.map((issue) => {
    const { index, ...publicIssue } = issue
    void index
    return publicIssue
  })
}

async function collectFiles(rootDir) {
  const out = []
  async function walk(dir) {
    const entries = await readdir(dir, { withFileTypes: true })
    for (const entry of entries) {
      const fullPath = path.join(dir, entry.name)
      if (entry.isDirectory()) {
        if (shouldScanPath(fullPath) || !SKIP_PATH_PARTS.has(entry.name)) await walk(fullPath)
        continue
      }
      if (shouldScanPath(fullPath)) out.push(fullPath)
    }
  }
  await walk(rootDir)
  return out
}

export async function findStaticTextIssues(
  rootDir = path.join(process.cwd(), 'src'),
  options = {},
) {
  const files = await collectFiles(rootDir)
  const issues = []
  for (const file of files) {
    const source = await readFile(file, 'utf8')
    issues.push(
      ...findStaticTextIssuesInSource(source, path.relative(process.cwd(), file), options),
    )
  }
  return issues
}

async function main() {
  const strictAscii =
    !process.argv.includes('--cjk-only') && process.env.I18N_STRICT_ASCII !== '0'
  const issues = await findStaticTextIssues(path.join(process.cwd(), 'src'), { strictAscii })
  if (issues.length === 0) return

  console.error(`Found ${issues.length} hard-coded user-facing text issue(s):`)
  for (const issue of issues) {
    console.error(`${issue.filePath}:${issue.line} [${issue.kind}] ${JSON.stringify(issue.text)}`)
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
