#!/usr/bin/env node

import { spawnSync } from 'node:child_process'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

import {
  assertE2ELaneNodeVersion,
  buildE2ELauncherEnvironment,
  buildExactLiveTitleFilter,
  buildLaneEnvironment,
  LIVE_E2E_CASES,
  normalizePlaywrightArguments,
  resolveE2EProject,
} from './e2e-lane-contract.mjs'

const frontendRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..')
const repoRoot = path.resolve(frontendRoot, '..')

export function parseArguments(lane, rawArguments) {
  const arguments_ = normalizePlaywrightArguments(rawArguments)
  let project
  const forwarded = []
  const consumedPolicy = new Set()
  for (let index = 0; index < arguments_.length; index += 1) {
    const argument = arguments_[index]
    if (argument === '--project') {
      project = arguments_[index + 1]
      index += 1
    } else if (argument.startsWith('--project=')) {
      project = argument.slice('--project='.length)
    } else if (argument === '--workers=1' || argument === '--retries=0') {
      if (consumedPolicy.has(argument)) {
        throw new Error('Duplicate Playwright execution policy override.')
      }
      consumedPolicy.add(argument)
    } else if (argument.startsWith('-')) {
      throw new Error('Only --workers=1 and --retries=0 execution options are accepted.')
    } else {
      forwarded.push(argument)
    }
  }
  return { project: resolveE2EProject(lane, project), forwarded }
}

export function collectJsonNodes(report) {
  const nodes = new Set()
  function visit(suites) {
    for (const suite of Array.isArray(suites) ? suites : []) {
      for (const spec of Array.isArray(suite.specs) ? suite.specs : []) {
        for (const test of Array.isArray(spec.tests) ? spec.tests : []) {
          const file = String(spec.file ?? suite.file ?? '').replaceAll('\\', '/')
          const marker = file.lastIndexOf('/e2e/')
          const normalizedFile =
            marker >= 0 ? file.slice(marker + 1) : `e2e/${path.posix.basename(file)}`
          const title = String(test.title ?? spec.title ?? '').trim()
          const project = String(test.projectName ?? '').trim()
          if (normalizedFile && title && project) {
            const identity = `${project}::${normalizedFile}::${title}`
            if (nodes.has(identity)) {
              throw new Error('The Playwright list contains a duplicate live selection identity.')
            }
            nodes.add(identity)
          }
        }
      }
      visit(suite.suites)
    }
  }
  visit(report?.suites)
  return [...nodes].sort()
}

function runResourceFreeLiveList(project, forwarded) {
  const required = ['E2E_LLM_BASE_URL', 'E2E_LLM_API_KEY', 'E2E_LLM_MODEL']
  if (
    !required.every((name) => typeof process.env[name] === 'string' && process.env[name].trim())
  ) {
    throw new Error(
      'The live E2E lane requires non-empty E2E_LLM_BASE_URL, E2E_LLM_API_KEY, and E2E_LLM_MODEL.',
    )
  }
  const environment = buildLaneEnvironment(
    'live',
    {
      ...process.env,
      E2E_LLM_BASE_URL: 'http://127.0.0.1:1/v1',
      E2E_LLM_API_KEY: 'selection-only',
      E2E_LLM_MODEL: 'selection-only',
    },
    project,
  )
  environment.E2E_SELECTION_ONLY = '1'
  environment.E2E_LIVE_CASES_JSON = JSON.stringify(LIVE_E2E_CASES)
  environment.E2E_LIVE_TITLE_FILTER = buildExactLiveTitleFilter()
  const result = spawnSync(
    'pnpm',
    [
      'exec',
      'playwright',
      'test',
      `--project=${project}`,
      '--workers=1',
      '--retries=0',
      '--reporter=json',
      ...forwarded,
    ],
    { cwd: frontendRoot, env: environment, encoding: 'utf8' },
  )
  if (result.error) throw result.error
  if (result.status !== 0) {
    process.stderr.write(result.stderr || 'Playwright live list failed.\n')
    return result.status ?? 1
  }
  const expected = LIVE_E2E_CASES.map(({ spec, title }) => `${project}::${spec}::${title}`).sort()
  const actual = collectJsonNodes(JSON.parse(result.stdout))
  if (JSON.stringify(actual) !== JSON.stringify(expected)) {
    throw new Error('The live E2E list did not select exactly the four configured cases.')
  }
  process.stdout.write(result.stdout)
  return 0
}

export function runIsolatedLane(
  lane,
  project,
  forwarded,
  manifest,
  inheritedEnvironment,
  spawnImplementation = spawnSync,
) {
  const environment = buildE2ELauncherEnvironment(lane, project, inheritedEnvironment)
  environment.E2E_RUN_MANIFEST = manifest
  environment.E2E_LIVE_CASES_JSON = JSON.stringify(LIVE_E2E_CASES)
  environment.E2E_LIVE_TITLE_FILTER = buildExactLiveTitleFilter()
  const result = spawnImplementation(
    path.join(repoRoot, 'scripts/run-isolated-e2e-tests.sh'),
    [lane, '--project', project, '--manifest', manifest, '--', ...forwarded],
    { cwd: repoRoot, env: environment, stdio: 'inherit' },
  )
  if (result.error) throw result.error
  return result.status ?? 1
}

export function run() {
  try {
    assertE2ELaneNodeVersion(process.version)
    const [lane, ...rawArguments] = process.argv.slice(2)
    if (lane !== 'scripted' && lane !== 'live') {
      throw new Error('Usage: node scripts/run-e2e-lane.mjs <scripted|live> [playwright options]')
    }
    const { project, forwarded } = parseArguments(lane, rawArguments)
    const listOnly = forwarded.length === 1 && forwarded[0] === '--list'
    if (lane === 'live' && listOnly) return runResourceFreeLiveList(project, forwarded)
    const manifest =
      process.env.E2E_RUN_MANIFEST ??
      `.omo/evidence/project-restart-consolidated-roadmap/e2e-${lane}-${process.pid}-${Date.now()}.json`
    return runIsolatedLane(lane, project, forwarded, manifest, process.env)
  } catch (error) {
    console.error(error instanceof Error ? error.message : 'Unable to start the E2E lane.')
    return 1
  }
}

if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  process.exitCode = run()
}
