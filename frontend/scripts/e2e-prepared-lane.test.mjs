import { spawnSync } from 'node:child_process'
import { mkdirSync, mkdtempSync, realpathSync, rmSync, symlinkSync, writeFileSync } from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

import { describe, expect, it } from 'vitest'

import { getE2ERunPaths } from './e2e-lane-contract.mjs'

const frontendRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..')

function preparedRoot() {
  const runRoot = mkdtempSync(path.join(frontendRoot, 'moldy-consumer-path-probe-'))
  for (const relative of ['frontend/auth', 'frontend/next/scripted', 'frontend/test-results/scripted']) {
    mkdirSync(path.join(runRoot, relative), { recursive: true })
  }
  return runRoot
}

describe('prepared E2E lane boundary', () => {
  it('derives auth, Next build, and results from one run root', () => {
    const runRoot = preparedRoot()
    try {
      expect(getE2ERunPaths('scripted', { MOLDY_TEST_RUN_ROOT: runRoot })).toEqual({
        authStatePath: `${realpathSync(runRoot)}/frontend/auth/scripted-user.json`,
        buildDir: `${realpathSync(runRoot)}/frontend/next/scripted`,
        resultsDir: `${realpathSync(runRoot)}/frontend/test-results/scripted`,
      })
    } finally {
      rmSync(runRoot, { recursive: true, force: true })
    }
  })

  it('rejects an unprepared isolated run root', () => {
    // Given: a wrapper root without the required frontend component tree.
    const runRoot = mkdtempSync(path.join(frontendRoot, 'moldy-consumer-path-probe-'))
    try {
      // When / Then: the lane contract fails before returning plain path strings.
      expect(() => getE2ERunPaths('scripted', { MOLDY_TEST_RUN_ROOT: runRoot })).toThrow()
    } finally {
      rmSync(runRoot, { recursive: true, force: true })
    }
  })

  it('rejects a prepared component replaced by a non-directory', () => {
    // Given: a prepared run root whose auth directory is replaced by a regular file.
    const runRoot = preparedRoot()
    rmSync(path.join(runRoot, 'frontend/auth'), { recursive: true })
    writeFileSync(path.join(runRoot, 'frontend/auth'), 'not-a-directory')
    try {
      // When / Then: consumer-time component validation rejects it.
      expect(() => getE2ERunPaths('scripted', { MOLDY_TEST_RUN_ROOT: runRoot })).toThrow()
    } finally {
      rmSync(runRoot, { recursive: true, force: true })
    }
  })

  it('returns only existing contained prepared directories', () => {
    // Given: a fully prepared frontend lane tree.
    const runRoot = preparedRoot()
    try {
      // When: the consumer resolves its run paths.
      const paths = getE2ERunPaths('scripted', { MOLDY_TEST_RUN_ROOT: runRoot })

      // Then: parents for auth, build, and results already exist beneath the root.
      expect(path.dirname(paths.authStatePath)).toBe(path.join(runRoot, 'frontend/auth'))
      expect(paths.buildDir).toBe(path.join(runRoot, 'frontend/next/scripted'))
      expect(paths.resultsDir).toBe(path.join(runRoot, 'frontend/test-results/scripted'))
    } finally {
      rmSync(runRoot, { recursive: true, force: true })
    }
  })

  it('rejects absolute and relative paths outside the run root', () => {
    const runRoot = preparedRoot()
    try {
      expect(() =>
        getE2ERunPaths('scripted', { MOLDY_TEST_RUN_ROOT: runRoot, E2E_RESULTS_DIR: '/tmp/outside' }),
      ).toThrow('must remain beneath MOLDY_TEST_RUN_ROOT')
      expect(() =>
        getE2ERunPaths('scripted', { MOLDY_TEST_RUN_ROOT: runRoot, E2E_RESULTS_DIR: '../outside' }),
      ).toThrow('must remain beneath MOLDY_TEST_RUN_ROOT')
    } finally {
      rmSync(runRoot, { recursive: true, force: true })
    }
  })

  it('resolves safe relative overrides from the run root', () => {
    const runRoot = preparedRoot()
    mkdirSync(path.join(runRoot, 'safe/results'), { recursive: true })
    try {
      const paths = getE2ERunPaths('scripted', {
        MOLDY_TEST_RUN_ROOT: runRoot,
        E2E_RESULTS_DIR: 'safe/results',
      })
      expect(paths.resultsDir).toBe(path.join(realpathSync(runRoot), 'safe/results'))
    } finally {
      rmSync(runRoot, { recursive: true, force: true })
    }
  })

  it('rejects a symlink escape and accepts a prepared safe descendant', () => {
    const runRoot = preparedRoot()
    const outside = mkdtempSync(path.join(frontendRoot, '.moldy-next-lane-probe-outside-'))
    try {
      symlinkSync(outside, path.join(runRoot, 'escape'), 'dir')
      mkdirSync(path.join(runRoot, 'not-created/results'), { recursive: true })
      expect(() =>
        getE2ERunPaths('scripted', {
          MOLDY_TEST_RUN_ROOT: runRoot,
          E2E_AUTH_STATE_PATH: path.join(runRoot, 'escape', 'auth.json'),
        }),
      ).toThrow('must remain beneath MOLDY_TEST_RUN_ROOT')
      expect(
        getE2ERunPaths('scripted', {
          MOLDY_TEST_RUN_ROOT: runRoot,
          E2E_RESULTS_DIR: path.join(runRoot, 'not-created', 'results'),
        }).resultsDir,
      ).toBe(path.join(realpathSync(runRoot), 'not-created', 'results'))
    } finally {
      rmSync(runRoot, { recursive: true, force: true })
      rmSync(outside, { recursive: true, force: true })
    }
  })

  it('loads a Next config whose distDir resolves to the prepared build directory', () => {
    const sourceFrontend = process.env.MOLDY_FRONTEND_SOURCE_ROOT ?? frontendRoot
    const runner = path.resolve(sourceFrontend, '../scripts/run-isolated-command.sh')
    const probe = [
      "import path from 'node:path'",
      "import {realpathSync} from 'node:fs'",
      "import configModule from 'next/dist/server/config.js'",
      "const config=await configModule.default('phase-production-build',process.cwd(),{silent:true})",
      "console.log('PROBE='+JSON.stringify({root:realpathSync(process.env.MOLDY_TEST_RUN_ROOT),distDir:config.distDir,resolved:path.join(process.cwd(),config.distDir)}))",
    ].join(';')
    const result = spawnSync(
      '/bin/bash',
      [runner, '--cwd', 'frontend', '--', process.execPath, '--input-type=module', '--eval', probe],
      { cwd: frontendRoot, env: { ...process.env, E2E_LANE: 'scripted' }, encoding: 'utf8' },
    )
    expect(result.status, result.stderr).toBe(0)
    const line = result.stdout.split('\n').find((value) => value.startsWith('PROBE='))
    expect(line).toBeDefined()
    const loaded = JSON.parse(line.slice('PROBE='.length))
    expect(path.isAbsolute(loaded.distDir)).toBe(false)
    expect(loaded.resolved).toBe(path.join(loaded.root, 'frontend', 'next', 'scripted'))
  })
})
