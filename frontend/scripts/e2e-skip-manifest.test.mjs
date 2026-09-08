import { describe, expect, it } from 'vitest'

import {
  checkRepository,
  collectSkipCallsFromSource,
  validateSkipManifest,
} from './check-e2e-skip-manifest.mjs'

const validRule = {
  id: 'backend',
  path_glob: 'e2e/**',
  condition: "process.env.PW_SKIP_BACKEND === '1'",
  reason: 'Requires backend',
  category: 'infrastructure',
  owner: 'quality-runtime',
  expires: '2027-03-31',
  expected_count: 1,
}
const call = {
  file: 'e2e/example.spec.ts',
  line: 3,
  condition: "process.env.PW_SKIP_BACKEND === '1'",
  reason: 'Requires backend',
}
const manifest = (rules) => ({ schema_version: 1, as_of: '2026-09-05', rules })

describe('E2E skip manifest', () => {
  it('classifies every current repository skip exactly once', async () => {
    await expect(checkRepository(process.cwd(), '2026-09-05')).resolves.toEqual({
      ruleCount: 10,
      skipCount: 83,
    })
  })

  it('rejects an unclassified skip', () => {
    expect(() =>
      validateSkipManifest({
        manifest: manifest([validRule]),
        calls: [{ ...call, reason: 'anonymous' }],
        today: '2026-09-05',
      }),
    ).toThrow('Unclassified')
  })

  it('enumerates test.fixme, describe.skip, and it.skip parser variants', () => {
    const calls = collectSkipCallsFromSource(
      `test.fixme(flag, 'one'); describe.skip(other, 'two'); it.skip(last, 'three')`,
      'e2e/example.spec.ts',
    )

    expect(calls.map(({ condition, reason }) => ({ condition, reason }))).toEqual([
      { condition: 'flag', reason: 'one' },
      { condition: 'other', reason: 'two' },
      { condition: 'last', reason: 'three' },
    ])
  })

  it('enumerates bracket-notation skip and fixme calls instead of bypassing governance', () => {
    const calls = collectSkipCallsFromSource(
      `test['skip'](flag, 'one'); describe["skip"](other, 'two'); it['fixme'](last, 'three')`,
      'e2e/example.spec.ts',
    )

    expect(calls.map(({ condition, reason }) => ({ condition, reason }))).toEqual([
      { condition: 'flag', reason: 'one' },
      { condition: 'other', reason: 'two' },
      { condition: 'last', reason: 'three' },
    ])
  })

  it('rejects expired metadata', () => {
    expect(() =>
      validateSkipManifest({
        manifest: manifest([{ ...validRule, expires: '2026-09-04' }]),
        calls: [call],
        today: '2026-09-05',
      }),
    ).toThrow('expired')
  })

  it.each([{ owner: '' }, { reason: '' }, { category: 'unknown' }, { expected_count: 0 }])(
    'rejects malformed metadata %#',
    (replacement) => {
      expect(() =>
        validateSkipManifest({
          manifest: manifest([{ ...validRule, ...replacement }]),
          calls: [call],
          today: '2026-09-05',
        }),
      ).toThrow('malformed')
    },
  )

  it('rejects an impossible calendar expiry', () => {
    expect(() =>
      validateSkipManifest({
        manifest: manifest([{ ...validRule, expires: '2027-99-99' }]),
        calls: [call],
        today: '2026-09-05',
      }),
    ).toThrow('malformed')
  })

  it('rejects unknown manifest and rule keys', () => {
    expect(() =>
      validateSkipManifest({
        manifest: { ...manifest([validRule]), extra: true },
        calls: [call],
        today: '2026-09-05',
      }),
    ).toThrow('schema')
    expect(() =>
      validateSkipManifest({
        manifest: manifest([{ ...validRule, extra: true }]),
        calls: [call],
        today: '2026-09-05',
      }),
    ).toThrow('malformed')
  })

  it('rejects ambiguous or unused broad rules', () => {
    expect(() =>
      validateSkipManifest({
        manifest: manifest([validRule, { ...validRule, id: 'duplicate-match' }]),
        calls: [call],
        today: '2026-09-05',
      }),
    ).toThrow('ambiguous')
    expect(() =>
      validateSkipManifest({
        manifest: manifest([{ ...validRule, expected_count: 2 }]),
        calls: [call],
        today: '2026-09-05',
      }),
    ).toThrow('expected 2')
  })
})
