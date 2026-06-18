#!/usr/bin/env node

import { spawnSync } from 'node:child_process'
import { readFile, writeFile } from 'node:fs/promises'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

const scriptDir = path.dirname(fileURLToPath(import.meta.url))
const rootDir = path.resolve(scriptDir, '..')
const baselinePath = path.join(scriptDir, 'jsx-a11y-baseline.json')
const updateBaseline = process.argv.includes('--update-baseline')

function normalizePath(filePath) {
  return path.relative(rootDir, filePath).split(path.sep).join('/')
}

function messageKey(message) {
  return `${message.ruleId}:${message.filePath}:${message.line}:${message.column}`
}

function summarizeByRule(messages) {
  const counts = new Map()
  for (const message of messages) {
    counts.set(message.ruleId, (counts.get(message.ruleId) ?? 0) + 1)
  }
  return [...counts.entries()].sort(([left], [right]) => left.localeCompare(right))
}

function printMessage(message, prefix = '') {
  console.log(
    `${prefix}${message.filePath}:${message.line}:${message.column} ${message.ruleId} ${message.message}`,
  )
}

async function loadBaseline() {
  try {
    const raw = await readFile(baselinePath, 'utf8')
    return JSON.parse(raw)
  } catch (error) {
    if (error && typeof error === 'object' && 'code' in error && error.code === 'ENOENT') {
      return []
    }
    throw error
  }
}

const eslintResult = spawnSync(
  'pnpm',
  ['exec', 'eslint', '-c', 'eslint.a11y.config.mjs', '--format', 'json', 'src/**/*.{tsx,jsx}'],
  {
    cwd: rootDir,
    encoding: 'utf8',
  },
)

let eslintReports
try {
  eslintReports = JSON.parse(eslintResult.stdout || '[]')
} catch {
  process.stderr.write(eslintResult.stdout)
  process.stderr.write(eslintResult.stderr)
  process.exit(eslintResult.status ?? 1)
}

const blockingEslintMessages = eslintReports.flatMap((report) =>
  report.messages
    .filter((message) => message.severity === 2 && !message.ruleId?.startsWith('jsx-a11y/'))
    .map((message) => ({
      filePath: normalizePath(report.filePath),
      line: message.line,
      column: message.column,
      ruleId: message.ruleId ?? 'eslint',
      message: message.message,
    })),
)

if (blockingEslintMessages.length > 0 || (eslintResult.status ?? 0) > 1) {
  console.error('ESLint failed before the JSX a11y baseline could be evaluated.')
  for (const message of blockingEslintMessages) {
    printMessage(message, '  ')
  }
  if (eslintResult.stderr) process.stderr.write(eslintResult.stderr)
  process.exit(eslintResult.status ?? 1)
}

const currentMessages = eslintReports
  .flatMap((report) =>
    report.messages
      .filter((message) => message.ruleId?.startsWith('jsx-a11y/'))
      .map((message) => ({
        filePath: normalizePath(report.filePath),
        line: message.line,
        column: message.column,
        ruleId: message.ruleId,
        message: message.message,
      })),
  )
  .sort((left, right) => messageKey(left).localeCompare(messageKey(right)))

if (updateBaseline) {
  await writeFile(baselinePath, `${JSON.stringify(currentMessages, null, 2)}\n`)
  console.log(`Updated JSX a11y baseline: ${currentMessages.length} warning(s).`)
  for (const [ruleId, count] of summarizeByRule(currentMessages)) {
    console.log(`  ${ruleId}: ${count}`)
  }
  process.exit(0)
}

const baseline = await loadBaseline()
const baselineKeys = new Set(baseline.map(messageKey))
const currentKeys = new Set(currentMessages.map(messageKey))
const newMessages = currentMessages.filter((message) => !baselineKeys.has(messageKey(message)))
const staleBaseline = baseline.filter((message) => !currentKeys.has(messageKey(message)))

console.log(`JSX a11y warnings: ${currentMessages.length}`)
for (const [ruleId, count] of summarizeByRule(currentMessages)) {
  console.log(`  ${ruleId}: ${count}`)
}

if (newMessages.length > 0 || staleBaseline.length > 0) {
  if (newMessages.length > 0) {
    console.error(`New JSX a11y warning(s): ${newMessages.length}`)
    for (const message of newMessages) {
      printMessage(message, '  ')
    }
  }
  if (staleBaseline.length > 0) {
    console.error(`Resolved JSX a11y baseline entries need removal: ${staleBaseline.length}`)
    for (const message of staleBaseline) {
      printMessage(message, '  ')
    }
  }
  console.error('Run `pnpm lint:a11y -- --update-baseline` only after reviewing the diff.')
  process.exit(1)
}

console.log(`JSX a11y guard passed with ${baseline.length} baseline warning(s).`)
