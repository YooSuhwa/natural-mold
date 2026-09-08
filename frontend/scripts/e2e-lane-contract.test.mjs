import {
  chmodSync,
  existsSync,
  mkdirSync,
  mkdtempSync,
  readFileSync,
  realpathSync,
  rmSync,
  symlinkSync,
  writeFileSync,
} from 'node:fs'
import { spawnSync } from 'node:child_process'
import os from 'node:os'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

import { describe, expect, it } from 'vitest'

import {
  assertIsolatedDatabaseEnvironment,
  assertE2ELaneNodeVersion,
  buildBackendWebServerCommand,
  buildE2ELauncherEnvironment,
  buildLaneEnvironment,
  buildFrontendWebServerCommand,
  buildPlaywrightProjectUse,
  buildExactLiveTitleFilter,
  E2E_PROJECTS,
  getE2EAuthStatePath,
  getE2ERunPaths,
  getLaneDefaultPorts,
  getPlaywrightExecutionPolicy,
  getPlaywrightWebServerTimeout,
  getPlaywrightArtifactsDirectory,
  LIVE_E2E_CASES,
  LIVE_E2E_SPECS,
  LIVE_E2E_SPEC_GLOBS,
  normalizePlaywrightArguments,
  resolveE2EProject,
  resolveConfiguredE2EProject,
  sanitizePlaywrightEnvironment,
  validatePlaywrightCliArguments,
} from './e2e-lane-contract.mjs'
import {
  authenticationSetupFailure,
  resolveSkillNodeModulesDirectory,
} from '../e2e/global-setup.mjs'
import { collectJsonNodes, parseArguments, runIsolatedLane } from './run-e2e-lane.mjs'

const frontendRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..')

describe('E2E lane contract', () => {
  it('consumes only the redundant fixed worker and retry options', () => {
    expect(
      parseArguments('scripted', [
        '--project=scripted-full',
        'e2e/smoke.spec.ts',
        '--workers=1',
        '--retries=0',
      ]),
    ).toEqual({ project: 'scripted-full', forwarded: ['e2e/smoke.spec.ts'] })
    expect(() => parseArguments('scripted', ['--workers=2'])).toThrow(
      'Only --workers=1 and --retries=0',
    )
    expect(() => parseArguments('scripted', ['--retries=1'])).toThrow(
      'Only --workers=1 and --retries=0',
    )
    expect(() => parseArguments('scripted', ['--workers', '1'])).toThrow(
      'Only --workers=1 and --retries=0',
    )
  })
  it('fails before lane startup unless the launcher uses Node 22', () => {
    // Given: supported and unsupported Node version strings from the launcher boundary.

    // When / Then: only major version 22 can continue, with a stable nonsecret failure message.
    expect(() => assertE2ELaneNodeVersion('v22.18.0')).not.toThrow()
    expect(() => assertE2ELaneNodeVersion('v25.8.1')).toThrow('E2E lanes require Node.js 22.')
    expect(() => assertE2ELaneNodeVersion('not-a-version')).toThrow('E2E lanes require Node.js 22.')
  })

  it('builds once with webpack and serves the isolated production frontend', () => {
    // Given: the configured chat runtime, backend origin, and frontend port.
    const command = buildFrontendWebServerCommand('langgraph_v3', 'http://localhost:8101', '3100')

    // When / Then: both build and server preserve the required startup environment.
    expect(command).toContain('pnpm prepare:assets')
    expect(command.match(/NEXT_PUBLIC_CHAT_RUNTIME=langgraph_v3/g)).toHaveLength(2)
    expect(command.match(/NEXT_PUBLIC_API_BASE_URL=http:\/\/localhost:8101/g)).toHaveLength(2)
    expect(command).toContain('pnpm exec next build --webpack')
    expect(command).toContain('pnpm exec next start --port 3100')
    expect(command).not.toContain('next dev')
  })

  it('keeps each project artifact policy compatible with export rules', () => {
    // Given: every configured Playwright project name.
    const smokeUse = buildPlaywrightProjectUse('scripted-smoke')
    const fullUse = buildPlaywrightProjectUse('scripted-full')
    const captureUse = buildPlaywrightProjectUse('scripted-capture')
    const liveUse = buildPlaywrightProjectUse('live-manual')

    // When / Then: capture alone retains diagnostics; all authenticated lanes turn both off.
    expect(smokeUse).toEqual({ browserName: 'chromium', trace: 'off', screenshot: 'off' })
    expect(fullUse).toEqual({ browserName: 'chromium', trace: 'off', screenshot: 'off' })
    expect(captureUse).toEqual({ browserName: 'chromium' })
    expect(liveUse).toEqual({ browserName: 'chromium', trace: 'off', screenshot: 'off' })
    expect(getPlaywrightExecutionPolicy()).toEqual({ workers: 1, retries: 0 })
  })

  it('gives production web servers enough time to build on a cold CI runner', () => {
    // Given: the production E2E lane builds Next.js before Playwright can connect.
    const config = readFileSync(path.join(frontendRoot, 'playwright.config.ts'), 'utf8')

    // When / Then: both owned servers use an explicit budget instead of Playwright's 60s default.
    expect(getPlaywrightWebServerTimeout()).toBe(180_000)
    expect(config).toContain('const webServerTimeout = getPlaywrightWebServerTimeout()')
    expect(config.match(/timeout: webServerTimeout/g)).toHaveLength(2)
  })

  it('validates CLI project selection only in the coordinator and trusts the worker project', () => {
    // Given: coordinator and worker copies of the same scripted project environment.
    const environment = { E2E_PROJECT: 'scripted-smoke', E2E_SELECTION_ONLY: '1' }

    // When / Then: coordinators require their exact CLI project selection.
    expect(() => resolveConfiguredE2EProject('scripted', environment, ['test', '--list'])).toThrow(
      'requires --project=scripted-smoke',
    )
    expect(() =>
      resolveConfiguredE2EProject('scripted', environment, [
        'test',
        '--project=scripted-full',
        '--list',
      ]),
    ).toThrow('requires --project=scripted-smoke')

    // Then: a real worker reload has no CLI selection but preserves its trusted project.
    expect(
      resolveConfiguredE2EProject('scripted', { ...environment, TEST_WORKER_INDEX: '0' }, ['test']),
    ).toBe('scripted-smoke')
    expect(() =>
      resolveConfiguredE2EProject(
        'scripted',
        { E2E_PROJECT: 'live-manual', TEST_WORKER_INDEX: '0' },
        ['test'],
      ),
    ).toThrow('not valid for the scripted lane')
  })

  it('uses the validated backend source for isolated skill-node dependencies', () => {
    // Given: an isolated mirror and its separate absolute backend source checkout.
    const runRoot = '/tmp/moldy-prepared-run'
    const backendSourceRoot = '/tmp/moldy-backend-source'

    // When: global setup resolves the skill-node dependency directory.
    const directory = resolveSkillNodeModulesDirectory({
      MOLDY_TEST_RUN_ROOT: runRoot,
      MOLDY_BACKEND_SOURCE_ROOT: backendSourceRoot,
    })

    // Then: it never derives the dependency path from the prepared frontend mirror.
    expect(directory).toBe('/tmp/moldy-backend-source/skill-node/node_modules')
    expect(directory).not.toContain('/tmp/moldy-prepared-run/backend')
  })

  it('rejects missing and relative backend sources only for isolated setup', () => {
    // Given: isolated setup with no backend source and a relative backend source.
    const isolated = { MOLDY_TEST_RUN_ROOT: '/tmp/moldy-prepared-run' }

    // When / Then: setup fails before checking a mirror-local nonexistent dependency directory.
    expect(() => resolveSkillNodeModulesDirectory(isolated)).toThrow('MOLDY_BACKEND_SOURCE_ROOT')
    expect(() =>
      resolveSkillNodeModulesDirectory({
        ...isolated,
        MOLDY_BACKEND_SOURCE_ROOT: '../backend',
      }),
    ).toThrow('must be absolute')
  })

  it('preserves repo-relative skill-node lookup outside isolated setup', () => {
    // Given: nonisolated setup with an unrelated backend-source environment value.
    const repositoryRoot = '/tmp/moldy-local-repository'

    // When: setup resolves the dependency location.
    const directory = resolveSkillNodeModulesDirectory(
      { MOLDY_BACKEND_SOURCE_ROOT: '/tmp/ignored-backend-source' },
      repositoryRoot,
    )

    // Then: the established repository-relative local path remains authoritative.
    expect(directory).toBe('/tmp/moldy-local-repository/backend/skill-node/node_modules')
  })

  it('uses the scripted model when LiteLLM variables are inherited', () => {
    // Given: a developer shell with a configured live LiteLLM lane.
    const inheritedEnvironment = {
      E2E_LLM_BASE_URL: 'https://litellm.example.test/v1',
      E2E_LLM_API_KEY: 'secret-not-logged',
      E2E_LLM_MODEL: 'gpt-test',
    }

    // When: the deterministic scripted lane is built.
    const environment = buildLaneEnvironment('scripted', inheritedEnvironment)

    // Then: no live seed input remains and the scripted seed is explicitly enabled.
    expect(environment.E2E_SCRIPTED_MODEL_ENABLED).toBe('true')
    expect(environment.E2E_LLM_BASE_URL).toBeUndefined()
    expect(environment.E2E_LLM_API_KEY).toBeUndefined()
    expect(environment.E2E_LLM_MODEL).toBeUndefined()
    expect(environment.OPENAI_API_KEY).toBeUndefined()
    expect(environment.TAVILY_API_KEY).toBeUndefined()
    expect(environment.E2E_PROJECT).toBe('scripted-full')
    expect(environment.E2E_FRONTEND_PORT).toBe('3100')
    expect(environment.E2E_BACKEND_PORT).toBe('8101')
  })

  it('keeps scripted webServer commands free of LiteLLM names while subprocess env strips them', () => {
    // Given: the scripted backend command source and a shell with live LiteLLM inputs.
    const scriptedBackendCommand = buildBackendWebServerCommand(
      'scripted',
      false,
      'http://localhost:3100,http://127.0.0.1:3100',
      8101,
    )
    const inheritedEnvironment = {
      E2E_LLM_BASE_URL: 'https://litellm.example.test/v1',
      E2E_LLM_API_KEY: 'secret-not-logged',
      E2E_LLM_MODEL: 'gpt-test',
    }

    // When: the scripted webServer command and lifecycle subprocess environment are resolved.
    const subprocessEnvironment = buildE2ELauncherEnvironment(
      'scripted',
      'scripted-full',
      inheritedEnvironment,
    )

    // Then: command logs contain only the scripted model flag and subprocess env remains keyless.
    expect(scriptedBackendCommand).toContain('E2E_SCRIPTED_MODEL_ENABLED=true')
    expect(scriptedBackendCommand).not.toMatch(/E2E_LLM_(BASE_URL|API_KEY|MODEL)/)
    expect(
      Object.keys(subprocessEnvironment).filter((name) => name.startsWith('E2E_LLM_')),
    ).toEqual([])
  })

  it('keeps provider and tool keys out of the scripted lifecycle launcher environment', () => {
    // Given: a developer shell with operator inputs and credential-shaped values.
    const inheritedEnvironment = {
      PATH: process.env.PATH ?? '',
      HOME: '/tmp/moldy-home',
      CI: 'true',
      DOCKER_HOST: 'unix:///var/run/docker.sock',
      DOCKER_CONFIG: '/tmp/attacker-docker-config',
      DOCKER_CONTEXT: 'attacker-context',
      MOLDY_GATE_DOCKER: '/usr/local/bin/docker',
      MOLDY_GATE_DOCKER_IDENTITY: 'a'.repeat(64),
      E2E_RUN_MANIFEST: '/tmp/manifest.json',
      E2E_TEST_TIMEOUT_MS: '60000',
      NEXT_PUBLIC_CHAT_RUNTIME: 'langgraph_v3',
      OPENAI_API_KEY: 'provider-sentinel',
      TAVILY_API_KEY: 'tool-sentinel',
      E2E_EGRESS_UPSTREAM_API_KEY: 'proxy-sentinel',
      OTEL_EXPORTER_OTLP_HEADERS: 'telemetry-sentinel',
      NODE_OPTIONS: '--trace-warnings',
    }

    // When: the direct scripted launcher environment is assembled.
    const environment = buildE2ELauncherEnvironment(
      'scripted',
      'scripted-full',
      inheritedEnvironment,
    )

    // Then: only nonsecret operator inputs remain for the Python lifecycle process.
    expect(environment).toMatchObject({
      PATH: inheritedEnvironment.PATH,
      HOME: inheritedEnvironment.HOME,
      CI: 'true',
      MOLDY_GATE_DOCKER: inheritedEnvironment.MOLDY_GATE_DOCKER,
      MOLDY_GATE_DOCKER_IDENTITY: inheritedEnvironment.MOLDY_GATE_DOCKER_IDENTITY,
      E2E_RUN_MANIFEST: inheritedEnvironment.E2E_RUN_MANIFEST,
      E2E_TEST_TIMEOUT_MS: '60000',
      NEXT_PUBLIC_CHAT_RUNTIME: 'langgraph_v3',
    })
    expect(environment.OPENAI_API_KEY).toBeUndefined()
    expect(environment.TAVILY_API_KEY).toBeUndefined()
    expect(environment.E2E_EGRESS_UPSTREAM_API_KEY).toBeUndefined()
    expect(environment.OTEL_EXPORTER_OTLP_HEADERS).toBeUndefined()
    expect(environment.NODE_OPTIONS).toBeUndefined()
    expect(environment.E2E_LLM_API_KEY).toBeUndefined()
    expect(environment.DOCKER_HOST).toBeUndefined()
    expect(environment.DOCKER_CONFIG).toBeUndefined()
    expect(environment.DOCKER_CONTEXT).toBeUndefined()
  })

  it('drops ambient Python controls before the fixed lifecycle derives its interpreter', () => {
    // Given: a parent shell that supplies Python controls outside the fixed lifecycle boundary.
    const inheritedEnvironment = {
      MOLDY_GATE_PYTHON: '/untrusted/gate-python',
      PYTHON: '/untrusted/python',
    }

    // When: the JavaScript launcher prepares the fixed Python lifecycle process.
    const launcherEnvironment = buildE2ELauncherEnvironment(
      'scripted',
      'scripted-full',
      inheritedEnvironment,
    )

    // Then: neither ambient Python selector crosses that trust boundary.
    expect(launcherEnvironment.MOLDY_GATE_PYTHON).toBeUndefined()
    expect(launcherEnvironment.PYTHON).toBeUndefined()

    // When: the fixed Python lifecycle supplies its own interpreter to Playwright.
    const workerEnvironment = sanitizePlaywrightEnvironment(
      'scripted',
      { ...launcherEnvironment, MOLDY_GATE_PYTHON: '/fixed/runner/python' },
      'scripted-full',
    )
    const command = buildBackendWebServerCommand(
      'scripted',
      false,
      'http://localhost:3100,http://127.0.0.1:3100',
      8101,
    )

    // Then: the runner interpreter reaches uvicorn without reopening PATH or invoking uv.
    expect(workerEnvironment.MOLDY_GATE_PYTHON).toBe('/fixed/runner/python')
    expect(workerEnvironment.PYTHON).toBeUndefined()
    expect(command).toContain('"${MOLDY_GATE_PYTHON:-./.venv/bin/python}" -m uvicorn')
    expect(command).not.toContain('uv run')
  })

  it('quotes the runner interpreter and uses the backend-local standalone fallback', () => {
    const tempRoot = mkdtempSync(path.join(os.tmpdir(), 'moldy-python-command-'))
    const backendRoot = path.join(tempRoot, 'backend')
    const fallback = path.join(backendRoot, '.venv', 'bin', 'python')
    const injectedMarker = path.join(tempRoot, 'injected-marker')
    const capturedArguments = path.join(tempRoot, 'arguments.txt')
    const hostileName = path.join(tempRoot, 'python $(touch injected-marker)')
    const command = buildBackendWebServerCommand(
      'scripted',
      false,
      'http://localhost:3100,http://127.0.0.1:3100',
      8101,
    )
    const script = '#!/bin/sh\nprintf "%s\\n" "$@" > "$MOLDY_CAPTURE_ARGS"\n'

    try {
      mkdirSync(path.dirname(fallback), { recursive: true })
      for (const executable of [hostileName, fallback]) {
        writeFileSync(executable, script, { mode: 0o700 })
        chmodSync(executable, 0o700)
      }

      for (const gatePython of [hostileName, undefined]) {
        rmSync(capturedArguments, { force: true })
        const environment = {
          PATH: '/usr/bin:/bin',
          MOLDY_CAPTURE_ARGS: capturedArguments,
          ...(gatePython ? { MOLDY_GATE_PYTHON: gatePython } : {}),
        }
        const result = spawnSync('/bin/sh', ['-ceu', command], {
          cwd: backendRoot,
          env: environment,
          encoding: 'utf8',
        })

        expect(result.status, result.stderr).toBe(0)
        expect(readFileSync(capturedArguments, 'utf8').trim().split('\n')).toEqual([
          '-m',
          'uvicorn',
          'app.main:app',
          '--port',
          '8101',
        ])
        expect(existsSync(injectedMarker)).toBe(false)
      }
    } finally {
      rmSync(tempRoot, { recursive: true, force: true })
    }
  })

  it('passes exactly the live connection triple only to the live lifecycle launcher', () => {
    // Given: a live shell with the required connection and unrelated provider values.
    const inheritedEnvironment = {
      E2E_LLM_BASE_URL: 'https://litellm.example.test/v1',
      E2E_LLM_API_KEY: 'live-connection-key',
      E2E_LLM_MODEL: 'gpt-test',
      OPENAI_API_KEY: 'provider-sentinel',
    }

    // When: the live launcher environment is assembled.
    const environment = buildE2ELauncherEnvironment('live', 'live-manual', inheritedEnvironment)

    // Then: the live connection is present without inheriting general provider keys.
    expect(Object.keys(environment).filter((name) => name.startsWith('E2E_LLM_'))).toEqual([
      'E2E_LLM_BASE_URL',
      'E2E_LLM_API_KEY',
      'E2E_LLM_MODEL',
    ])
    expect(environment.OPENAI_API_KEY).toBeUndefined()
  })

  it('passes the capture tour only as its exact scripted-capture opt-in', () => {
    // Given: a capture launcher request with the documented opt-in value.
    const inheritedEnvironment = { E2E_CAPTURE_TOUR: '1' }

    // When: the direct launcher environment is assembled.
    const environment = buildE2ELauncherEnvironment(
      'scripted',
      'scripted-capture',
      inheritedEnvironment,
    )

    // Then: the lifecycle receives that one nonsecret capture control.
    expect(environment.E2E_CAPTURE_TOUR).toBe('1')
  })

  it('passes an exact owned-loopback opt-in for a live launcher', () => {
    // Given: live launcher inputs with a canonical boolean opt-in.
    const inheritedEnvironment = {
      E2E_LLM_BASE_URL: 'https://litellm.example.test/v1',
      E2E_LLM_API_KEY: 'live-connection-key',
      E2E_LLM_MODEL: 'gpt-test',
      E2E_EGRESS_ALLOW_OWNED_LOOPBACK: '1',
    }

    // When: the launcher environment is assembled.
    const environment = buildE2ELauncherEnvironment('live', 'live-manual', inheritedEnvironment)

    // Then: the boolean wire value reaches the isolated lifecycle process.
    expect(environment.E2E_EGRESS_ALLOW_OWNED_LOOPBACK).toBe('1')
  })

  it('drops a noncanonical owned-loopback opt-in from a live launcher', () => {
    // Given: live launcher inputs with a noncanonical opt-in value.
    const inheritedEnvironment = {
      E2E_LLM_BASE_URL: 'https://litellm.example.test/v1',
      E2E_LLM_API_KEY: 'live-connection-key',
      E2E_LLM_MODEL: 'gpt-test',
      E2E_EGRESS_ALLOW_OWNED_LOOPBACK: 'true',
    }

    // When: the launcher environment is assembled.
    const environment = buildE2ELauncherEnvironment('live', 'live-manual', inheritedEnvironment)

    // Then: an ambiguous value never crosses the environment boundary.
    expect(environment.E2E_EGRESS_ALLOW_OWNED_LOOPBACK).toBeUndefined()
  })

  it('spawns the direct scripted lifecycle command with the filtered environment', () => {
    // Given: a direct scripted launch with a credential-shaped parent environment.
    const launches = []
    const inheritedEnvironment = {
      PATH: process.env.PATH ?? '',
      DOCKER_HOST: 'tcp://127.0.0.1:65535',
      MOLDY_GATE_DOCKER: '/usr/local/bin/docker',
      MOLDY_GATE_DOCKER_IDENTITY: 'a'.repeat(64),
      OPENAI_API_KEY: 'provider-sentinel',
      TAVILY_API_KEY: 'tool-sentinel',
    }

    // When: the launcher invokes the lifecycle command through its process seam.
    const status = runIsolatedLane(
      'scripted',
      'scripted-smoke',
      [],
      '/tmp/moldy-manifest.json',
      inheritedEnvironment,
      (...arguments_) => {
        launches.push(arguments_)
        return { status: 0 }
      },
    )

    // Then: the spawned command receives no provider or tool sentinel.
    expect(status).toBe(0)
    expect(launches).toHaveLength(1)
    const [, , options] = launches[0]
    expect(options.env.OPENAI_API_KEY).toBeUndefined()
    expect(options.env.TAVILY_API_KEY).toBeUndefined()
    expect(options.env.DOCKER_HOST).toBeUndefined()
    expect(options.env.MOLDY_GATE_DOCKER).toBe('/usr/local/bin/docker')
    expect(options.env.MOLDY_GATE_DOCKER_IDENTITY).toBe('a'.repeat(64))
    expect(options.env.E2E_RUN_MANIFEST).toBe('/tmp/moldy-manifest.json')
  })

  it('rejects duplicate live selection identities instead of collapsing them', () => {
    // Given: a Playwright JSON list with two physical leaves sharing one identity.
    const report = {
      suites: [
        {
          specs: [
            {
              file: 'e2e/builder.spec.ts',
              title: 'starts a session and runs the build pipeline from an initial message',
              tests: [{ projectName: 'live-manual' }, { projectName: 'live-manual' }],
            },
          ],
        },
      ],
    }

    // When / Then: exact-live verification fails rather than silently de-duplicating it.
    expect(() => collectJsonNodes(report)).toThrow('duplicate live selection identity')
  })

  it('isolates non-capture Playwright output from exportable results', () => {
    // Given: a prepared isolated root with both exportable and disposable artifact directories.
    const runRoot = mkdtempSync(path.join(os.tmpdir(), 'moldy-artifact-output-'))
    for (const relative of [
      'frontend/auth/scripted-smoke',
      'frontend/.next/scripted-smoke',
      'frontend/test-results/scripted-smoke',
      'frontend/auth/scripted-capture',
      'frontend/.next/scripted-capture',
      'frontend/test-results/scripted-capture',
      'frontend/playwright-artifacts/scripted-smoke',
    ]) {
      mkdirSync(path.join(runRoot, relative), { recursive: true })
    }
    const environment = { MOLDY_TEST_RUN_ROOT: runRoot }

    // When: configuration resolves one non-capture and one capture project output directory.
    const isolatedOutput = getPlaywrightArtifactsDirectory(
      'scripted',
      environment,
      'scripted-smoke',
    )
    const captureOutput = getPlaywrightArtifactsDirectory(
      'scripted',
      environment,
      'scripted-capture',
    )

    // Then: only capture retains the exportable result-tree output convention.
    const resolvedRunRoot = realpathSync(runRoot)
    expect(isolatedOutput).toBe(
      path.join(resolvedRunRoot, 'frontend/playwright-artifacts/scripted-smoke'),
    )
    expect(isolatedOutput).not.toContain('frontend/test-results/scripted-smoke')
    expect(captureOutput).toBe(
      path.join(resolvedRunRoot, 'frontend/test-results/scripted-capture/playwright-artifacts'),
    )

    // When: Playwright clears the disposable non-capture leaf before its configuration reload.
    rmSync(isolatedOutput, { recursive: true })
    const recreatedOutput = getPlaywrightArtifactsDirectory(
      'scripted',
      environment,
      'scripted-smoke',
    )

    // Then: the prepared parent remains trusted while Playwright can recreate its owned leaf.
    expect(recreatedOutput).toBe(isolatedOutput)

    // When / Then: an attacker cannot replace that leaf with a file or symbolic link.
    writeFileSync(recreatedOutput, 'not-a-directory')
    expect(() =>
      getPlaywrightArtifactsDirectory('scripted', environment, 'scripted-smoke'),
    ).toThrow('without symbolic links')
    rmSync(recreatedOutput)
    symlinkSync(path.join(runRoot, 'frontend/test-results/scripted-smoke'), recreatedOutput, 'dir')
    expect(() =>
      getPlaywrightArtifactsDirectory('scripted', environment, 'scripted-smoke'),
    ).toThrow('without symbolic links')
    rmSync(runRoot, { recursive: true, force: true })
  })

  it.each(['E2E_LLM_BASE_URL', 'E2E_LLM_API_KEY', 'E2E_LLM_MODEL'])(
    'rejects a live lane when %s is missing',
    (missingName) => {
      // Given: a live lane missing exactly one required connection value.
      const incompleteEnvironment = {
        E2E_LLM_BASE_URL: 'https://litellm.example.test/v1',
        E2E_LLM_API_KEY: 'secret-not-logged',
        E2E_LLM_MODEL: 'gpt-test',
      }
      delete incompleteEnvironment[missingName]

      // When / Then: startup fails without revealing the supplied secret.
      expect(() => buildLaneEnvironment('live', incompleteEnvironment)).toThrow(
        'E2E_LLM_BASE_URL, E2E_LLM_API_KEY, and E2E_LLM_MODEL',
      )
    },
  )

  it('returns a stable safe authentication failure from the observable status', () => {
    // Given: global setup has only a failure label and HTTP status to report.

    // When: global setup turns the response status into an error.
    const failure = authenticationSetupFailure('E2E authentication setup', 401)

    // Then: the caller receives only the safe failure contract.
    expect(failure.message).toBe('E2E authentication setup failed (401).')
    expect(failure.message).not.toContain('secret')
  })

  it('selects a real model only in the live lane', () => {
    // Given: a complete live model configuration.
    const liveEnvironment = {
      E2E_LLM_BASE_URL: 'https://litellm.example.test/v1',
      E2E_LLM_API_KEY: 'secret-not-logged',
      E2E_LLM_MODEL: 'gpt-test',
    }

    // When: the live lane is built.
    const environment = buildLaneEnvironment('live', liveEnvironment)

    // Then: the scripted seed is explicitly disabled.
    expect(environment.E2E_SCRIPTED_MODEL_ENABLED).toBe('false')
    expect(environment.E2E_FRONTEND_PORT).toBe('3200')
    expect(environment.E2E_BACKEND_PORT).toBe('8201')
    expect(environment.E2E_PROJECT).toBe('live-manual')
  })

  it('rejects a backend lane without explicit async and sync URLs', () => {
    // Given: only one of the two checkpointer database URLs is configured.
    const incompleteEnvironment = {
      DATABASE_URL: 'postgresql+asyncpg://moldy:moldy@localhost:5433/moldy',
    }

    // When / Then: the runner fails before it can use a shared .env database.
    expect(() => assertIsolatedDatabaseEnvironment('scripted', incompleteEnvironment)).toThrow(
      'DATABASE_URL and DATABASE_URL_SYNC',
    )
  })

  it('rejects database URLs with different targets', () => {
    // Given: the application and checkpointer would connect to different databases.
    const mismatchedEnvironment = {
      DATABASE_URL: 'postgresql+asyncpg://moldy:moldy@localhost:5433/moldy_e2e_scripted_local',
      DATABASE_URL_SYNC: 'postgresql://moldy:moldy@localhost:5433/moldy_e2e_scripted_other',
    }

    // When / Then: the runner fails before servers start and without disclosing either URL.
    expect(() => assertIsolatedDatabaseEnvironment('scripted', mismatchedEnvironment)).toThrow(
      'same host, port, and database',
    )
  })

  it('contains only the four explicitly selected live scenarios', () => {
    // Given / When: the live suite contract is read.

    // Then: it cannot silently expand to every scripted scenario.
    expect(LIVE_E2E_CASES).toEqual([
      {
        spec: 'e2e/builder.spec.ts',
        title: 'starts a session and runs the build pipeline from an initial message',
      },
      {
        spec: 'e2e/operator-screens.spec.ts',
        title: 'System LLM shows the seed-configured role slots',
      },
      {
        spec: 'e2e/operator-screens.spec.ts',
        title: 'creates and deletes a system credential through the catalog modal',
      },
      {
        spec: 'e2e/agent-triggers.spec.ts',
        title: 'a created interval trigger renders in the settings triggers tab',
      },
    ])
    expect(LIVE_E2E_SPECS).toEqual([
      'e2e/builder.spec.ts',
      'e2e/operator-screens.spec.ts',
      'e2e/agent-triggers.spec.ts',
    ])
    expect(LIVE_E2E_SPEC_GLOBS).toEqual([
      '**/builder.spec.ts',
      '**/operator-screens.spec.ts',
      '**/agent-triggers.spec.ts',
    ])
  })

  it('creates a regex-escaped, end-anchored filter for every exact live title', () => {
    // Given: the checked four-case manifest and a title containing regex metacharacters.
    const customCases = [{ spec: 'e2e/example.spec.ts', title: 'keeps (only) $5 [safe]?' }]

    // When: the runner filter is derived.
    const filter = buildExactLiveTitleFilter(customCases)

    // Then: each wanted title matches at the end while near-matches do not.
    expect(filter).toBe('keeps \\(only\\) \\$5 \\[safe\\]\\?$')
    expect(new RegExp(filter).test('suite keeps (only) $5 [safe]?')).toBe(true)
    expect(new RegExp(filter).test('keeps (only) $5 [safe]')).toBe(false)
    expect(new RegExp(filter).test('keeps (only) $5 [safe]? extra')).toBe(false)
  })

  it('validates the exact project set and lane-compatible defaults', () => {
    // Given: the declared Playwright project inventory.

    // When: each lane resolves its implicit project.
    const scriptedProject = resolveE2EProject('scripted')
    const liveProject = resolveE2EProject('live')

    // Then: all and only the roadmap projects exist and lane crossings fail.
    expect(E2E_PROJECTS).toEqual([
      'scripted-smoke',
      'scripted-full',
      'scripted-capture',
      'live-manual',
    ])
    expect(scriptedProject).toBe('scripted-full')
    expect(liveProject).toBe('live-manual')
    expect(resolveE2EProject('scripted', 'scripted-capture')).toBe('scripted-capture')
    expect(() => resolveE2EProject('scripted', 'live-manual')).toThrow(
      'not valid for the scripted lane',
    )
    expect(() => resolveE2EProject('live', 'unknown')).toThrow('unknown E2E project')
  })

  it('routes the named LangGraph v3 suite through functional and capture projects', () => {
    // Given: the user-facing package scripts for the LangGraph v3 E2E suite.
    const packageJson = JSON.parse(readFileSync(path.join(frontendRoot, 'package.json'), 'utf8'))

    // When / Then: the aggregate command preserves both isolated project-specific runs.
    expect(packageJson.scripts['test:e2e:langgraph-v3']).toBe(
      'pnpm test:e2e:langgraph-v3:functional && pnpm test:e2e:langgraph-v3:capture',
    )
    expect(packageJson.scripts['test:e2e:langgraph-v3:functional']).toBe(
      'NEXT_PUBLIC_CHAT_RUNTIME=langgraph_v3 node scripts/run-e2e-lane.mjs scripted e2e/chat-langgraph-v3.spec.ts e2e/draft-conversation-langgraph-v3.spec.ts e2e/chat-transcript-stability.spec.ts',
    )
    expect(packageJson.scripts['test:e2e:langgraph-v3:capture']).toBe(
      'NEXT_PUBLIC_CHAT_RUNTIME=langgraph_v3 E2E_CAPTURE_TOUR=1 node scripts/run-e2e-lane.mjs scripted --project=scripted-capture e2e/chat-langgraph-v3-visual-matrix.spec.ts',
    )
  })

  it('rejects CLI selection and execution overrides outside the lane contract', () => {
    // Given: a scripted-full invocation with an exact selected project.
    const expectedProject = 'scripted-full'

    // When / Then: only the runner's exact concurrency settings are accepted.
    expect(() =>
      validatePlaywrightCliArguments(
        'scripted',
        expectedProject,
        ['test', '--project=scripted-full', '--workers=1', '--retries=0', 'e2e/smoke.spec.ts'],
        false,
      ),
    ).not.toThrow()
    expect(() =>
      validatePlaywrightCliArguments(
        'scripted',
        expectedProject,
        ['test', '--project=live-manual'],
        false,
      ),
    ).toThrow('requires --project=scripted-full')
    expect(() =>
      validatePlaywrightCliArguments(
        'scripted',
        expectedProject,
        ['test', '--project=scripted-capture'],
        false,
      ),
    ).toThrow('requires --project=scripted-full')
    expect(() =>
      validatePlaywrightCliArguments(
        'scripted',
        expectedProject,
        ['test', '--project=scripted-full', '--workers=2'],
        false,
      ),
    ).toThrow('workers=1')
    expect(() =>
      validatePlaywrightCliArguments(
        'scripted',
        expectedProject,
        ['test', '--project=scripted-full', '--retries=1'],
        false,
      ),
    ).toThrow('retries=0')
    expect(() =>
      validatePlaywrightCliArguments(
        'scripted',
        expectedProject,
        ['test', '--project=scripted-full', '--ui'],
        false,
      ),
    ).toThrow('does not allow --ui')
    expect(() =>
      validatePlaywrightCliArguments(
        'scripted',
        expectedProject,
        ['test', '--project=scripted-full', '--reporter=json'],
        false,
      ),
    ).toThrow('does not allow --reporter')
    expect(() =>
      validatePlaywrightCliArguments(
        'scripted',
        expectedProject,
        ['test', '--project=scripted-full', 'e2e/../operator-screens.spec.ts'],
        false,
      ),
    ).toThrow('normalized scripted spec selection')
    expect(() =>
      validatePlaywrightCliArguments(
        'live',
        'live-manual',
        ['test', '--project=live-manual', 'e2e/builder.spec.ts'],
        false,
      ),
    ).toThrow('does not allow direct spec or title selection')
  })

  it('allows the resource-free selection-only list receipt path and nothing broader', () => {
    // Given: the isolated runner's selection-only invocation.
    const arguments_ = [
      'test',
      '--project=live-manual',
      '--workers=1',
      '--retries=0',
      '--reporter=json',
      '--list',
    ]

    // When / Then: its exact machine-readable list contract is accepted.
    expect(() =>
      validatePlaywrightCliArguments('live', 'live-manual', arguments_, true),
    ).not.toThrow()
    expect(() =>
      validatePlaywrightCliArguments('live', 'live-manual', arguments_.slice(0, -1), true),
    ).toThrow('requires --list')
    expect(() =>
      validatePlaywrightCliArguments(
        'live',
        'live-manual',
        [...arguments_.slice(0, -1), '--grep', 'anything', '--list'],
        true,
      ),
    ).toThrow('does not allow --grep')
  })

  it('builds a worker environment from the explicit run-scoped allowlist', () => {
    // Given: a shell carrying provider, tool, telemetry, and process-control secrets.
    const inheritedEnvironment = {
      DATABASE_URL: 'postgresql+asyncpg://moldy:moldy@localhost:5433/moldy_e2e_live_unit',
      DATABASE_URL_SYNC: 'postgresql://moldy:moldy@localhost:5433/moldy_e2e_live_unit',
      ENCRYPTION_KEYS: 'test-encryption-key',
      JWT_SECRET: 'test-jwt-secret',
      E2E_LLM_BASE_URL: 'http://127.0.0.1:4567/v1',
      E2E_LLM_API_KEY: 'proxy-token',
      E2E_LLM_MODEL: 'test-model',
      OPENAI_API_KEY: 'provider-secret',
      TAVILY_API_KEY: 'tool-secret',
      OTEL_EXPORTER_OTLP_HEADERS: 'telemetry-secret',
      E2E_EGRESS_UPSTREAM_API_KEY: 'upstream-secret',
      NODE_OPTIONS: '--trace-warnings',
      PW_SKIP_BACKEND: '1',
      PATH: process.env.PATH ?? '',
    }

    // When: the Playwright worker environment is prepared.
    const environment = sanitizePlaywrightEnvironment('live', inheritedEnvironment, 'live-manual')

    // Then: required run-scoped values remain while inherited credentials and controls do not.
    expect(environment).toMatchObject({
      DATABASE_URL: inheritedEnvironment.DATABASE_URL,
      DATABASE_URL_SYNC: inheritedEnvironment.DATABASE_URL_SYNC,
      ENCRYPTION_KEYS: inheritedEnvironment.ENCRYPTION_KEYS,
      JWT_SECRET: inheritedEnvironment.JWT_SECRET,
      E2E_LLM_API_KEY: 'proxy-token',
    })
    expect(environment.OPENAI_API_KEY).toBeUndefined()
    expect(environment.TAVILY_API_KEY).toBeUndefined()
    expect(environment.OTEL_EXPORTER_OTLP_HEADERS).toBeUndefined()
    expect(environment.E2E_EGRESS_UPSTREAM_API_KEY).toBeUndefined()
    expect(environment.NODE_OPTIONS).toBeUndefined()
    expect(environment.PW_SKIP_BACKEND).toBeUndefined()
  })

  it('replaces live model inputs during resource-free selection', () => {
    // Given: a live shell configured with a real upstream endpoint and key.
    const inheritedEnvironment = {
      E2E_LLM_BASE_URL: 'https://litellm.example.test/v1',
      E2E_LLM_API_KEY: 'upstream-secret',
      E2E_LLM_MODEL: 'upstream-model',
      E2E_SELECTION_ONLY: '1',
    }

    // When: the resource-free selector prepares Playwright.
    const environment = sanitizePlaywrightEnvironment('live', inheritedEnvironment, 'live-manual')

    // Then: neither upstream endpoint nor key reaches config loading.
    expect(environment.E2E_LLM_BASE_URL).toBe('http://127.0.0.1:1/v1')
    expect(environment.E2E_LLM_API_KEY).toBe('selection-only')
    expect(environment.E2E_LLM_MODEL).toBe('selection-only')
  })

  it('allows only the capture tour opt-in value for the capture project', () => {
    // Given: capture and full lane environments with an explicit tour input.
    const enabledCapture = { E2E_CAPTURE_TOUR: '1' }

    // When / Then: capture accepts its exact opt-in, while other values and projects fail early.
    expect(
      sanitizePlaywrightEnvironment('scripted', enabledCapture, 'scripted-capture'),
    ).toMatchObject({ E2E_CAPTURE_TOUR: '1' })
    expect(() =>
      sanitizePlaywrightEnvironment('scripted', { E2E_CAPTURE_TOUR: 'true' }, 'scripted-capture'),
    ).toThrow('E2E_CAPTURE_TOUR=1')
    expect(() =>
      sanitizePlaywrightEnvironment('scripted', enabledCapture, 'scripted-full'),
    ).toThrow('only valid for scripted-capture')
  })

  it('rejects a live worker that tries to reach the upstream instead of its local proxy', () => {
    // Given: a normal live worker environment with an upstream LiteLLM URL.
    const inheritedEnvironment = {
      E2E_LLM_BASE_URL: 'https://litellm.example.test/v1',
      E2E_LLM_API_KEY: 'upstream-secret',
      E2E_LLM_MODEL: 'upstream-model',
    }

    // When / Then: config preparation stops before any worker or web server can inherit it.
    expect(() =>
      sanitizePlaywrightEnvironment('live', inheritedEnvironment, 'live-manual'),
    ).toThrow('local egress proxy')
  })

  it('removes the package-manager argument separator before invoking Playwright', () => {
    // Given: pnpm forwarded a Playwright option after its own separator.
    const forwardedArguments = ['--', '--list']

    // When: the lane runner prepares Playwright arguments.
    const playwrightArguments = normalizePlaywrightArguments(forwardedArguments)

    // Then: Playwright receives the option rather than an inert positional value.
    expect(playwrightArguments).toEqual(['--list'])
  })

  it('keeps explicit port overrides for each lane', () => {
    // Given: a caller that runs both lanes with non-default port pairs.
    const overrides = { E2E_FRONTEND_PORT: '3300', E2E_BACKEND_PORT: '8301' }

    // When: the live lane is built.
    const environment = buildLaneEnvironment('live', {
      ...overrides,
      E2E_LLM_BASE_URL: 'https://litellm.example.test/v1',
      E2E_LLM_API_KEY: 'secret-not-logged',
      E2E_LLM_MODEL: 'gpt-test',
    })

    // Then: the caller-owned port pair wins over the live defaults.
    expect(environment).toMatchObject(overrides)
    expect(getLaneDefaultPorts('scripted')).toEqual({ frontend: '3100', backend: '8101' })
    expect(getLaneDefaultPorts('live')).toEqual({ frontend: '3200', backend: '8201' })
  })

  it('namespaces auth state and Playwright output by lane while preserving an explicit auth override', () => {
    // Given: both lanes run from the same frontend checkout.
    const scripted = buildLaneEnvironment('scripted', {})
    const live = buildLaneEnvironment('live', {
      E2E_LLM_BASE_URL: 'https://litellm.example.test/v1',
      E2E_LLM_API_KEY: 'secret-not-logged',
      E2E_LLM_MODEL: 'gpt-test',
    })

    // When: each lane derives the auth state passed to config, setup, and raw capture contexts.
    const scriptedAuthPath = getE2EAuthStatePath('scripted', scripted)
    const liveAuthPath = getE2EAuthStatePath('live', live)

    // Then: neither session nor Playwright result path can collide across lanes.
    expect(scriptedAuthPath).toBe('./e2e/.auth/scripted-user.json')
    expect(liveAuthPath).toBe('./e2e/.auth/live-user.json')
    expect(scripted.E2E_AUTH_STATE_PATH).toBe(scriptedAuthPath)
    expect(live.E2E_AUTH_STATE_PATH).toBe(liveAuthPath)
    expect(scriptedAuthPath).not.toBe(liveAuthPath)
    expect(getE2EAuthStatePath('live', { E2E_AUTH_STATE_PATH: './tmp/live-auth.json' })).toBe(
      './tmp/live-auth.json',
    )
  })

  it('rejects driver and database-name contracts that could target a shared database', () => {
    // Given: a correct target but an unsafe async driver and a non-lane database name.
    const unsafeEnvironment = {
      DATABASE_URL: 'postgresql://moldy:moldy@localhost:5433/moldy',
      DATABASE_URL_SYNC: 'postgresql://moldy:moldy@localhost:5433/moldy',
    }

    // When / Then: the runner rejects the unsafe connection contract before startup.
    expect(() => assertIsolatedDatabaseEnvironment('scripted', unsafeEnvironment)).toThrow(
      'postgresql+asyncpg',
    )

    const checkpointerIncompatibleEnvironment = {
      DATABASE_URL: 'postgresql+asyncpg://moldy:moldy@localhost:5433/moldy_e2e_scripted_local',
      DATABASE_URL_SYNC: 'postgresql+psycopg://moldy:moldy@localhost:5433/moldy_e2e_scripted_local',
    }
    expect(() =>
      assertIsolatedDatabaseEnvironment('scripted', checkpointerIncompatibleEnvironment),
    ).toThrow('postgresql://')

    const sharedNameEnvironment = {
      DATABASE_URL: 'postgresql+asyncpg://moldy:moldy@localhost:5433/moldy',
      DATABASE_URL_SYNC: 'postgresql://moldy:moldy@localhost:5433/moldy',
    }
    expect(() => assertIsolatedDatabaseEnvironment('scripted', sharedNameEnvironment)).toThrow(
      'moldy_e2e_scripted',
    )

    const lookalikeNameEnvironment = {
      DATABASE_URL: 'postgresql+asyncpg://moldy:moldy@localhost:5433/moldy_e2e_scriptedshared',
      DATABASE_URL_SYNC: 'postgresql://moldy:moldy@localhost:5433/moldy_e2e_scriptedshared',
    }
    expect(() => assertIsolatedDatabaseEnvironment('scripted', lookalikeNameEnvironment)).toThrow(
      'moldy_e2e_scripted',
    )
  })

  it('preserves default bytes and safe explicit path precedence', () => {
    // Given: no wrapper root and an explicit legacy auth override.

    // When: lane paths are resolved.
    const defaults = getE2ERunPaths('live', {})
    const overridden = getE2ERunPaths('live', { E2E_AUTH_STATE_PATH: './tmp/live-auth.json' })

    // Then: existing dev/default values are byte-equivalent and overrides still win.
    expect(defaults).toEqual({
      authStatePath: './e2e/.auth/live-user.json',
      buildDir: '.next',
      resultsDir: 'test-results/live',
    })
    expect(getPlaywrightArtifactsDirectory('live', {}, 'live-manual')).toBe(
      'test-results/live/playwright-artifacts',
    )
    expect(overridden.authStatePath).toBe('./tmp/live-auth.json')
  })
})
