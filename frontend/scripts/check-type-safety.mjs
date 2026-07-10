#!/usr/bin/env node

import { readdir, readFile } from 'node:fs/promises'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import ts from 'typescript'

const SCANNED_EXTENSIONS = new Set(['.ts', '.tsx', '.mts', '.cts'])

const SCAN_ROOTS = ['src', 'e2e', 'tests']

const SKIP_PATH_PARTS = new Set([
  '.next',
  'coverage',
  'node_modules',
  'playwright-report',
  'test-results',
])

const TS_SUPPRESSION_RE = /@ts-(?:ignore|expect-error|nocheck)\b/g

// Tests may need @ts-expect-error to simulate states the types forbid on
// purpose (e.g. deleting window for an SSR path). Allow it there only when a
// reason is written next to the directive; @ts-ignore/@ts-nocheck and
// production code stay banned.
const TEST_FILE_RE = /(?:\.(?:test|spec)\.[cm]?tsx?$|(?:^|\/)__tests__\/)/
const MIN_SUPPRESSION_REASON_LENGTH = 3

function isAllowedTestSuppression(filePath, source, matchIndex, matchText) {
  if (!matchText.includes('expect-error')) return false
  if (!TEST_FILE_RE.test(filePath)) return false
  const lineEnd = source.indexOf('\n', matchIndex)
  const rest = source.slice(
    matchIndex + matchText.length,
    lineEnd === -1 ? undefined : lineEnd,
  )
  const reason = rest.replace(/^[\s:—-]+/, '').trim()
  return reason.length >= MIN_SUPPRESSION_REASON_LENGTH
}

function normalizePath(filePath) {
  return filePath.split(path.sep).join('/')
}

function shouldScanPath(filePath) {
  const normalized = normalizePath(filePath)
  const parts = normalized.split('/')
  if (parts.some((part) => SKIP_PATH_PARTS.has(part))) return false
  return SCANNED_EXTENSIONS.has(path.extname(normalized))
}

function scriptKindFor(filePath) {
  if (filePath.endsWith('.tsx')) return ts.ScriptKind.TSX
  if (filePath.endsWith('.mts')) return ts.ScriptKind.TS
  if (filePath.endsWith('.cts')) return ts.ScriptKind.TS
  return ts.ScriptKind.TS
}

function lineInfo(sourceFile, index) {
  const pos = sourceFile.getLineAndCharacterOfPosition(index)
  return {
    line: pos.line + 1,
    column: pos.character + 1,
  }
}

function snippetAt(source, index) {
  const lineStart = source.lastIndexOf('\n', index) + 1
  const lineEnd = source.indexOf('\n', index)
  return source.slice(lineStart, lineEnd === -1 ? undefined : lineEnd).trim()
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

function findTypeSafetyIssues(source, filePath) {
  const sourceFile = ts.createSourceFile(
    filePath,
    source,
    ts.ScriptTarget.Latest,
    true,
    scriptKindFor(filePath),
  )
  const issues = []

  for (const match of source.matchAll(TS_SUPPRESSION_RE)) {
    const index = match.index ?? 0
    if (isAllowedTestSuppression(filePath, source, index, match[0])) continue
    const { line, column } = lineInfo(sourceFile, index)
    issues.push({
      filePath,
      line,
      column,
      rule: 'ts-suppression-comment',
      message: 'fix the type instead of suppressing TypeScript diagnostics',
      snippet: snippetAt(source, index),
    })
  }

  function visit(node) {
    if (node.kind === ts.SyntaxKind.AnyKeyword) {
      const index = node.getStart(sourceFile)
      const { line, column } = lineInfo(sourceFile, index)
      issues.push({
        filePath,
        line,
        column,
        rule: 'explicit-any',
        message: 'use unknown plus a type guard, or model the value explicitly',
        snippet: snippetAt(source, index),
      })
    }

    if (ts.isNonNullExpression(node)) {
      const index = node.getStart(sourceFile)
      const { line, column } = lineInfo(sourceFile, index)
      issues.push({
        filePath,
        line,
        column,
        rule: 'non-null-assertion',
        message: 'narrow the value before use instead of using postfix !',
        snippet: snippetAt(source, index),
      })
    }

    ts.forEachChild(node, visit)
  }

  visit(sourceFile)
  return issues
}

async function findIssues() {
  const rootDir = process.cwd()
  const files = []
  for (const scanRoot of SCAN_ROOTS) {
    files.push(...(await collectFiles(path.join(rootDir, scanRoot))))
  }

  const issues = []
  for (const file of files) {
    const source = await readFile(file, 'utf8')
    const filePath = normalizePath(path.relative(rootDir, file))
    issues.push(...findTypeSafetyIssues(source, filePath))
  }

  return issues.sort((left, right) =>
    left.filePath === right.filePath
      ? left.line - right.line || left.column - right.column
      : left.filePath.localeCompare(right.filePath),
  )
}

async function main() {
  const issues = await findIssues()

  if (issues.length === 0) {
    console.log('Type-safety guard passed.')
    return
  }

  console.error(`Found ${issues.length} type-safety issue(s):`)
  for (const issue of issues) {
    console.error(
      `${issue.filePath}:${issue.line}:${issue.column} [${issue.rule}] ${issue.message} :: ${issue.snippet}`,
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
