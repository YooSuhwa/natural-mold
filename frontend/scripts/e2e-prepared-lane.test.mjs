import { spawnSync } from 'node:child_process'
import {
  existsSync,
  mkdirSync,
  mkdtempSync,
  realpathSync,
  rmSync,
  symlinkSync,
  writeFileSync,
} from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

import { describe, expect, it } from 'vitest'

import { getE2ERunPaths } from './e2e-lane-contract.mjs'

const frontendRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..')

function collectListedNodes(report) {
  const nodes = []
  function visit(suites) {
    for (const suite of suites ?? []) {
      for (const spec of suite.specs ?? []) {
        for (const test of spec.tests ?? []) {
          nodes.push({
            file: String(spec.file),
            project: String(test.projectName),
            title: String(spec.title),
          })
        }
      }
      visit(suite.suites)
    }
  }
  visit(report.suites)
  return nodes
}

function listProject(lane, project, arguments_ = [], additionalEnvironment = {}) {
  const result = spawnSync(
    'pnpm',
    [
      'exec',
      'playwright',
      'test',
      '--list',
      '--reporter=json',
      `--project=${project}`,
      '--workers=1',
      '--retries=0',
      ...arguments_,
    ],
    {
      cwd: frontendRoot,
      env: {
        ...process.env,
        ...additionalEnvironment,
        E2E_LANE: lane,
        E2E_PROJECT: project,
        E2E_SELECTION_ONLY: '1',
      },
      encoding: 'utf8',
    },
  )
  return result
}

function preparedRoot() {
  const runRoot = mkdtempSync(path.join(frontendRoot, 'moldy-consumer-path-probe-'))
  for (const relative of [
    'frontend/auth/scripted-full',
    'frontend/.next/scripted-full',
    'frontend/test-results/scripted-full',
  ]) {
    mkdirSync(path.join(runRoot, relative), { recursive: true })
  }
  return runRoot
}

describe('prepared E2E lane boundary', () => {
  it('derives auth, Next build, and results from one run root', () => {
    const runRoot = preparedRoot()
    try {
      expect(getE2ERunPaths('scripted', { MOLDY_TEST_RUN_ROOT: runRoot })).toEqual({
        authStatePath: `${realpathSync(runRoot)}/frontend/auth/scripted-full/scripted-user.json`,
        buildDir: `${realpathSync(runRoot)}/frontend/.next/scripted-full`,
        resultsDir: `${realpathSync(runRoot)}/frontend/test-results/scripted-full`,
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
    rmSync(path.join(runRoot, 'frontend/auth/scripted-full'), { recursive: true })
    writeFileSync(path.join(runRoot, 'frontend/auth/scripted-full'), 'not-a-directory')
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
      expect(path.dirname(paths.authStatePath)).toBe(
        path.join(runRoot, 'frontend/auth/scripted-full'),
      )
      expect(paths.buildDir).toBe(path.join(runRoot, 'frontend/.next/scripted-full'))
      expect(paths.resultsDir).toBe(path.join(runRoot, 'frontend/test-results/scripted-full'))
    } finally {
      rmSync(runRoot, { recursive: true, force: true })
    }
  })

  it('rejects absolute and relative paths outside the run root', () => {
    const runRoot = preparedRoot()
    try {
      expect(() =>
        getE2ERunPaths('scripted', {
          MOLDY_TEST_RUN_ROOT: runRoot,
          E2E_RESULTS_DIR: '/tmp/outside',
        }),
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

  it('namespaces isolated paths by project without changing local dev defaults', () => {
    // Given: a prepared root with two scripted project directories.
    const runRoot = preparedRoot()
    for (const relative of [
      'frontend/auth/scripted-capture',
      'frontend/.next/scripted-capture',
      'frontend/test-results/scripted-capture',
    ]) {
      mkdirSync(path.join(runRoot, relative), { recursive: true })
    }
    try {
      // When: the same lane resolves each supported project.
      const full = getE2ERunPaths('scripted', {
        MOLDY_TEST_RUN_ROOT: runRoot,
        E2E_PROJECT: 'scripted-full',
      })
      const capture = getE2ERunPaths('scripted', {
        MOLDY_TEST_RUN_ROOT: runRoot,
        E2E_PROJECT: 'scripted-capture',
      })

      // Then: isolated artifacts cannot collide while nonisolated compatibility remains unchanged.
      expect(full.resultsDir).not.toBe(capture.resultsDir)
      expect(getE2ERunPaths('scripted', {})).toEqual({
        authStatePath: './e2e/.auth/scripted-user.json',
        buildDir: '.next',
        resultsDir: 'test-results/scripted',
      })
    } finally {
      rmSync(runRoot, { recursive: true, force: true })
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
    expect(loaded.resolved).toBe(path.join(loaded.root, 'frontend', '.next', 'scripted-full'))
  })

  it('lists a named project without database or server lifecycle side effects', () => {
    // Given: selection mode without database credentials or running web servers.
    const result = spawnSync(
      'pnpm',
      ['exec', 'playwright', 'test', '--list', '--project=scripted-smoke'],
      {
        cwd: frontendRoot,
        env: {
          ...process.env,
          E2E_LANE: 'scripted',
          E2E_PROJECT: 'scripted-smoke',
          E2E_SELECTION_ONLY: '1',
        },
        encoding: 'utf8',
      },
    )

    // When / Then: the project can be inspected without opening its normal runtime resources.
    expect(result.status, result.stderr).toBe(0)
    expect(result.stdout).toContain('[scripted-smoke]')
  })

  it('keeps the prepared result parent when Playwright clears its disposable output child', () => {
    // Given: an isolated prepared root whose Playwright output child already exists.
    const runRoot = preparedRoot()
    const resultRoot = path.join(runRoot, 'frontend/test-results/scripted-full')
    const outputChild = path.join(resultRoot, 'playwright-artifacts')
    mkdirSync(outputChild, { recursive: true })
    try {
      // When: the actual Playwright config loads through a resource-free project list.
      const result = listProject('scripted', 'scripted-full', [], {
        MOLDY_TEST_RUN_ROOT: runRoot,
        MOLDY_BACKEND_SOURCE_ROOT: path.resolve(frontendRoot, '../backend'),
      })

      // Then: the prepared parent survives Playwright's output-directory lifecycle.
      expect(result.status, result.stderr).toBe(0)
      expect(existsSync(resultRoot)).toBe(true)
    } finally {
      rmSync(runRoot, { recursive: true, force: true })
    }
  })

  it('selects all and only the four configured projects with their exact topology', () => {
    // Given: resource-free list requests for every supported lane project.
    const selections = new Map(
      [
        ['scripted', 'scripted-smoke'],
        ['scripted', 'scripted-full'],
        ['scripted', 'scripted-capture'],
        ['live', 'live-manual'],
      ].map(([lane, project]) => {
        const result = listProject(lane, project)
        expect(result.status, result.stderr).toBe(0)
        return [project, collectListedNodes(JSON.parse(result.stdout))]
      }),
    )

    // When: the four project selections are observed through Playwright itself.
    const smoke = selections.get('scripted-smoke')
    const full = selections.get('scripted-full')
    const capture = selections.get('scripted-capture')
    const live = selections.get('live-manual')

    // Then: each project has one identity and the configured file/title boundary.
    for (const [project, nodes] of selections) {
      expect(nodes).toBeDefined()
      expect(new Set(nodes.map((node) => node.project))).toEqual(new Set([project]))
    }
    expect(smoke.every((node) => node.file === 'smoke.spec.ts')).toBe(true)
    expect(full.some((node) => /(^|\/)(captures|manual)/.test(node.file))).toBe(false)
    expect(full.some((node) => node.file.includes('live'))).toBe(false)
    expect(capture.every((node) => node.file.startsWith('captures/'))).toBe(true)
    expect(live).toEqual([
      {
        file: 'agent-triggers.spec.ts',
        project: 'live-manual',
        title: 'a created interval trigger renders in the settings triggers tab',
      },
      {
        file: 'builder.spec.ts',
        project: 'live-manual',
        title: 'starts a session and runs the build pipeline from an initial message',
      },
      {
        file: 'operator-screens.spec.ts',
        project: 'live-manual',
        title: 'System LLM shows the seed-configured role slots',
      },
      {
        file: 'operator-screens.spec.ts',
        project: 'live-manual',
        title: 'creates and deletes a system credential through the catalog modal',
      },
    ])
  })

  it('rejects unknown, cross-lane, and concurrency-changing CLI selections before resources start', () => {
    // Given: invalid project and execution override requests in resource-free mode.
    const invalidProject = listProject('scripted', 'scripted-unlisted')
    const crossLaneProject = listProject('scripted', 'live-manual')
    const workerOverride = listProject('scripted', 'scripted-smoke', ['--workers=2'])
    const retryOverride = listProject('scripted', 'scripted-smoke', ['--retries=1'])

    // When / Then: Playwright config rejects each invocation before web servers or setup run.
    expect(invalidProject.status).not.toBe(0)
    expect(crossLaneProject.status).not.toBe(0)
    expect(workerOverride.status).not.toBe(0)
    expect(retryOverride.status).not.toBe(0)
    expect(workerOverride.stderr).toContain('--workers=1')
    expect(retryOverride.stderr).toContain('--retries=0')
  })

  it('allows canonical scripted smoke narrowing but rejects traversal and live narrowing', () => {
    // Given: one normalized scripted selector plus unsafe cross-boundary selectors.
    const smoke = listProject('scripted', 'scripted-smoke', ['e2e/smoke.spec.ts'])
    const traversal = listProject('scripted', 'scripted-smoke', ['e2e/../smoke.spec.ts'])
    const liveNarrowing = listProject('live', 'live-manual', ['e2e/builder.spec.ts'])

    // When / Then: only the scoped scripted file reaches resource-free selection.
    expect(smoke.status, smoke.stderr).toBe(0)
    const smokeNodes = collectListedNodes(JSON.parse(smoke.stdout))
    expect(smokeNodes.length).toBeGreaterThan(0)
    expect(smokeNodes.every((node) => node.file === 'smoke.spec.ts')).toBe(true)
    expect(traversal.status).not.toBe(0)
    expect(liveNarrowing.status).not.toBe(0)
    expect(traversal.stderr).toContain('normalized scripted spec selection')
    expect(liveNarrowing.stderr).toContain('does not allow direct spec or title selection')
  })
})
