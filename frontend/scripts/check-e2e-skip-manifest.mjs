#!/usr/bin/env node

import { readdir, readFile } from 'node:fs/promises'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import ts from 'typescript'

const CATEGORIES = new Set([
  'credentialed-live',
  'infrastructure',
  'legacy-matrix',
  'manual-capture',
])
const RULE_KEYS = [
  'category',
  'condition',
  'expected_count',
  'expires',
  'id',
  'owner',
  'path_glob',
  'reason',
]

function normalize(value) {
  return value.replace(/\s+/g, ' ').trim()
}

function matchesGlob(filePath, glob) {
  if (glob.endsWith('/**')) return filePath.startsWith(glob.slice(0, -2))
  if (glob === 'e2e/**') return filePath.startsWith('e2e/')
  return filePath === glob
}

async function collectSpecPaths(rootDir) {
  const files = []
  async function walk(directory) {
    for (const entry of await readdir(directory, { withFileTypes: true })) {
      const fullPath = path.join(directory, entry.name)
      if (entry.isDirectory()) await walk(fullPath)
      else if (entry.name.endsWith('.spec.ts')) files.push(fullPath)
    }
  }
  await walk(path.join(rootDir, 'e2e'))
  return files.sort()
}

export function collectSkipCallsFromSource(source, filePath) {
  const sourceFile = ts.createSourceFile(
    filePath,
    source,
    ts.ScriptTarget.Latest,
    true,
    ts.ScriptKind.TS,
  )
  const calls = []
  function visit(node) {
    let member
    let receiver
    if (ts.isCallExpression(node)) {
      if (ts.isPropertyAccessExpression(node.expression)) {
        member = node.expression.name.text
        receiver = node.expression.expression
      } else if (
        ts.isElementAccessExpression(node.expression) &&
        node.expression.argumentExpression &&
        ts.isStringLiteralLike(node.expression.argumentExpression)
      ) {
        member = node.expression.argumentExpression.text
        receiver = node.expression.expression
      }
    }
    if (
      ts.isCallExpression(node) &&
      (member === 'skip' || member === 'fixme') &&
      receiver &&
      ['test', 'it', 'describe'].includes(receiver.getText(sourceFile).split('.')[0])
    ) {
      const [conditionNode, reasonNode] = node.arguments
      const position = sourceFile.getLineAndCharacterOfPosition(node.getStart(sourceFile))
      calls.push({
        file: filePath,
        line: position.line + 1,
        condition: conditionNode ? normalize(conditionNode.getText(sourceFile)) : '',
        reason: reasonNode && ts.isStringLiteralLike(reasonNode) ? reasonNode.text : '',
      })
    }
    ts.forEachChild(node, visit)
  }
  visit(sourceFile)
  return calls
}

function parseManifest(raw, today) {
  if (
    !raw ||
    Object.keys(raw).sort().join(',') !== 'as_of,rules,schema_version' ||
    raw.schema_version !== 1 ||
    !isCalendarDate(raw.as_of) ||
    raw.as_of > today ||
    !Array.isArray(raw.rules)
  ) {
    throw new Error('Skip manifest schema is invalid.')
  }
  const ids = new Set()
  return raw.rules.map((rule) => {
    if (
      !rule ||
      Object.keys(rule).sort().join(',') !== RULE_KEYS.join(',') ||
      typeof rule.id !== 'string' ||
      !rule.id ||
      ids.has(rule.id) ||
      typeof rule.path_glob !== 'string' ||
      typeof rule.condition !== 'string' ||
      typeof rule.reason !== 'string' ||
      !rule.reason.trim() ||
      !CATEGORIES.has(rule.category) ||
      typeof rule.owner !== 'string' ||
      !rule.owner.trim() ||
      !isCalendarDate(rule.expires) ||
      typeof rule.expected_count !== 'number' ||
      !Number.isInteger(rule.expected_count) ||
      rule.expected_count < 1
    ) {
      throw new Error('Skip manifest contains malformed or duplicate metadata.')
    }
    if (rule.expires < today) throw new Error(`Skip rule ${rule.id} expired on ${rule.expires}.`)
    ids.add(rule.id)
    return rule
  })
}

function isCalendarDate(value) {
  if (typeof value !== 'string' || !/^\d{4}-\d{2}-\d{2}$/.test(value)) return false
  const date = new Date(`${value}T00:00:00Z`)
  return !Number.isNaN(date.valueOf()) && date.toISOString().slice(0, 10) === value
}

export function validateSkipManifest({ manifest, calls, today }) {
  const rules = parseManifest(manifest, today)
  const counts = new Map(rules.map((rule) => [rule.id, 0]))
  for (const call of calls) {
    const matches = rules.filter(
      (rule) =>
        matchesGlob(call.file, rule.path_glob) &&
        call.condition === rule.condition &&
        call.reason === rule.reason,
    )
    if (matches.length !== 1) {
      throw new Error(`Unclassified or ambiguous skip at ${call.file}:${call.line}.`)
    }
    const rule = matches[0]
    counts.set(rule.id, (counts.get(rule.id) ?? 0) + 1)
  }
  for (const rule of rules) {
    if (counts.get(rule.id) !== rule.expected_count) {
      throw new Error(
        `Skip rule ${rule.id} expected ${rule.expected_count}, found ${counts.get(rule.id)}.`,
      )
    }
  }
  return { ruleCount: rules.length, skipCount: calls.length }
}

export async function checkRepository(
  rootDir,
  today = new Intl.DateTimeFormat('en-CA', { timeZone: 'Asia/Seoul' }).format(new Date()),
) {
  const manifest = JSON.parse(await readFile(path.join(rootDir, 'e2e/skip-manifest.json'), 'utf8'))
  const calls = []
  for (const absolutePath of await collectSpecPaths(rootDir)) {
    const relativePath = absolutePath
      .slice(rootDir.length + 1)
      .split(path.sep)
      .join('/')
    calls.push(...collectSkipCallsFromSource(await readFile(absolutePath, 'utf8'), relativePath))
  }
  return validateSkipManifest({ manifest, calls, today })
}

async function main() {
  const result = await checkRepository(process.cwd())
  console.log(`E2E skip manifest passed: ${result.skipCount} skips in ${result.ruleCount} rules.`)
}

if (process.argv[1] === fileURLToPath(import.meta.url)) {
  await main()
}
