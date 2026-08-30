import { describe, expect, it } from 'vitest'

import {
  assertIsolatedDatabaseEnvironment,
  buildLaneEnvironment,
  getE2EAuthStatePath,
  getLaneDefaultPorts,
  LIVE_E2E_SPECS,
  LIVE_E2E_TEST_MATCH,
  normalizePlaywrightArguments,
} from './e2e-lane-contract.mjs'

describe('E2E lane contract', () => {
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
    expect(environment.E2E_FRONTEND_PORT).toBe('3100')
    expect(environment.E2E_BACKEND_PORT).toBe('8101')
  })

  it('rejects a live lane with an incomplete LiteLLM configuration', () => {
    // Given: a live lane missing one required connection value.
    const incompleteEnvironment = {
      E2E_LLM_BASE_URL: 'https://litellm.example.test/v1',
      E2E_LLM_API_KEY: 'secret-not-logged',
    }

    // When / Then: startup fails without revealing the supplied secret.
    expect(() => buildLaneEnvironment('live', incompleteEnvironment)).toThrow(
      'E2E_LLM_BASE_URL, E2E_LLM_API_KEY, and E2E_LLM_MODEL',
    )
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
  })

  it('rejects a backend lane without explicit async and sync URLs', () => {
    // Given: only one of the two checkpointer database URLs is configured.
    const incompleteEnvironment = { DATABASE_URL: 'postgresql+asyncpg://moldy:moldy@localhost:5433/moldy' }

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

  it('contains only the explicitly selected live scenarios', () => {
    // Given / When: the live suite contract is read.

    // Then: it cannot silently expand to every scripted scenario.
    expect(LIVE_E2E_SPECS).toEqual([
      'e2e/builder.spec.ts',
      'e2e/operator-screens.spec.ts',
      'e2e/agent-triggers.spec.ts',
    ])
    expect(LIVE_E2E_TEST_MATCH).toEqual([
      '**/builder.spec.ts',
      '**/operator-screens.spec.ts',
      '**/agent-triggers.spec.ts',
    ])
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
    expect(() => assertIsolatedDatabaseEnvironment('scripted', checkpointerIncompatibleEnvironment)).toThrow(
      'postgresql://',
    )

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
})
