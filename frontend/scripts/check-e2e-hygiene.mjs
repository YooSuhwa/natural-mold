#!/usr/bin/env node

import { readdir, readFile } from 'node:fs/promises'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import ts from 'typescript'

const SCANNED_EXTENSIONS = new Set(['.ts', '.tsx'])
const SCAN_ROOTS = ['e2e', 'tests']
const SKIP_PATH_PARTS = new Set([
  '.next',
  'coverage',
  'node_modules',
  'playwright-report',
  'test-results',
])

function normalizePath(filePath) {
  return filePath.split(path.sep).join('/')
}

function shouldScanPath(filePath) {
  const normalized = normalizePath(filePath)
  if (normalized.split('/').some((part) => SKIP_PATH_PARTS.has(part))) return false
  return SCANNED_EXTENSIONS.has(path.extname(normalized))
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

function propertyName(node) {
  if (!ts.isPropertyAccessExpression(node)) return null
  return node.name.text
}

function expressionRootName(node) {
  let current = node
  while (ts.isPropertyAccessExpression(current)) {
    current = current.expression
  }
  return ts.isIdentifier(current) ? current.text : null
}

function isTestOrDescribeExpression(node) {
  const rootName = expressionRootName(node)
  return rootName === 'test' || rootName === 'describe'
}

function isStringLiteralLike(node) {
  return ts.isStringLiteral(node) || ts.isNoSubstitutionTemplateLiteral(node)
}

function hasNonEmptyStringLiteral(node) {
  return isStringLiteralLike(node) && node.text.trim().length > 0
}

function issue(sourceFile, source, filePath, node, rule, message) {
  const index = node.getStart(sourceFile)
  const { line, column } = lineInfo(sourceFile, index)
  return {
    filePath,
    line,
    column,
    rule,
    message,
    snippet: snippetAt(source, index),
  }
}

function skipNeedsReview(callExpression) {
  const args = callExpression.arguments
  if (args.length < 2) return true

  const firstArg = args[0]
  const secondArg = args[1]

  if (firstArg && isStringLiteralLike(firstArg)) return true

  return !secondArg || !hasNonEmptyStringLiteral(secondArg)
}

function findE2eHygieneIssues(source, filePath) {
  const sourceFile = ts.createSourceFile(
    filePath,
    source,
    ts.ScriptTarget.Latest,
    true,
    ts.ScriptKind.TSX,
  )
  const issues = []

  function visit(node) {
    if (ts.isCallExpression(node)) {
      const expression = node.expression

      if (ts.isPropertyAccessExpression(expression)) {
        const name = propertyName(expression)

        if (name === 'only' && isTestOrDescribeExpression(expression)) {
          issues.push(
            issue(
              sourceFile,
              source,
              filePath,
              expression,
              'focused-test',
              'remove test.only/describe.only before committing',
            ),
          )
        }

        if (name === 'skip' && isTestOrDescribeExpression(expression) && skipNeedsReview(node)) {
          issues.push(
            issue(
              sourceFile,
              source,
              filePath,
              expression,
              'unreviewed-skip',
              'use test.skip(condition, "reason") with an explicit non-empty reason',
            ),
          )
        }

        if (name === 'waitForTimeout') {
          issues.push(
            issue(
              sourceFile,
              source,
              filePath,
              expression,
              'fixed-timeout',
              'wait for a visible state, request, response, poll condition, or next paint instead',
            ),
          )
        }
      }
    }

    if (
      ts.isPropertyAssignment(node) &&
      ts.isIdentifier(node.name) &&
      node.name.text === 'force' &&
      node.initializer.kind === ts.SyntaxKind.TrueKeyword
    ) {
      issues.push(
        issue(
          sourceFile,
          source,
          filePath,
          node.name,
          'forced-action',
          'avoid force: true; make the element actionable or document a narrow helper exception',
        ),
      )
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
    issues.push(...findE2eHygieneIssues(source, filePath))
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
    console.log('E2E hygiene guard passed.')
    return
  }

  console.error(`Found ${issues.length} E2E hygiene issue(s):`)
  for (const item of issues) {
    console.error(
      `${item.filePath}:${item.line}:${item.column} [${item.rule}] ${item.message} :: ${item.snippet}`,
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
