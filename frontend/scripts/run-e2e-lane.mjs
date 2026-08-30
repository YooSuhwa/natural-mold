#!/usr/bin/env node

import { spawnSync } from 'node:child_process'

import {
  assertIsolatedDatabaseEnvironment,
  buildLaneEnvironment,
  LIVE_E2E_SPECS,
  normalizePlaywrightArguments,
} from './e2e-lane-contract.mjs'

const [lane, ...forwardedArguments] = process.argv.slice(2)
const playwrightArguments = normalizePlaywrightArguments(forwardedArguments)

function run() {
  try {
    if (lane !== 'scripted' && lane !== 'live') {
      throw new Error('Usage: node scripts/run-e2e-lane.mjs <scripted|live> [playwright options]')
    }

    const environment = buildLaneEnvironment(lane, process.env)
    if (environment.PW_SKIP_BACKEND !== '1') assertIsolatedDatabaseEnvironment(lane, environment)

    const playwrightCommand = [
      'exec',
      'playwright',
      'test',
      ...(lane === 'live' ? LIVE_E2E_SPECS : []),
      ...playwrightArguments,
    ]
    const result = spawnSync('pnpm', playwrightCommand, {
      cwd: process.cwd(),
      env: environment,
      stdio: 'inherit',
    })

    if (result.error) throw result.error
    return result.status ?? 1
  } catch (error) {
    console.error(error instanceof Error ? error.message : 'Unable to start the E2E lane.')
    return 1
  }
}

process.exitCode = run()
