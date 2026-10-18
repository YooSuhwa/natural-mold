import { describe, expect, it } from 'vitest'

import { buildE2ELauncherEnvironment, sanitizePlaywrightEnvironment } from './e2e-lane-contract.mjs'

describe('E2E_ORG_AUTHZ opt-in', () => {
  it('reaches the scripted-full Playwright and launcher environments', () => {
    const inherited = { E2E_ORG_AUTHZ: '1' }

    expect(sanitizePlaywrightEnvironment('scripted', inherited, 'scripted-full')).toMatchObject({
      E2E_ORG_AUTHZ: '1',
    })
    expect(buildE2ELauncherEnvironment('scripted', 'scripted-full', inherited)).toMatchObject({
      E2E_ORG_AUTHZ: '1',
    })
  })

  it('rejects other values and projects', () => {
    expect(() =>
      sanitizePlaywrightEnvironment('scripted', { E2E_ORG_AUTHZ: 'true' }, 'scripted-full'),
    ).toThrow('E2E_ORG_AUTHZ=1')
    expect(() =>
      sanitizePlaywrightEnvironment('scripted', { E2E_ORG_AUTHZ: '1' }, 'scripted-smoke'),
    ).toThrow('only valid for scripted-full')
  })

  it('stays absent unless opted in', () => {
    expect(sanitizePlaywrightEnvironment('scripted', {}, 'scripted-full').E2E_ORG_AUTHZ).toBe(
      undefined,
    )
  })
})
