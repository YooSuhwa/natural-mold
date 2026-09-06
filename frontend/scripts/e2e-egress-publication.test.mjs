// @vitest-environment node

import {
  existsSync,
  mkdtempSync,
  readFileSync,
  readdirSync,
  rmSync,
  statSync,
  writeFileSync,
} from 'node:fs'
import { tmpdir } from 'node:os'
import path from 'node:path'
import { describe, expect, it } from 'vitest'

import { writeExclusiveJson } from './e2e-egress-policy.mjs'

describe('E2E egress readiness publication', () => {
  it('keeps the target absent until complete 0600 JSON is ready to publish', () => {
    const runRoot = mkdtempSync(path.join(tmpdir(), 'moldy-egress-publication-'))
    const readyFile = path.join(runRoot, 'ready.json')
    let visibleDuringSerialization

    try {
      const ready = {
        toJSON() {
          visibleDuringSerialization = existsSync(readyFile)
          return { pid: 123, proxyBaseUrl: 'http://127.0.0.1:9999/v1', ready: true }
        },
      }

      writeExclusiveJson(readyFile, ready)

      expect(visibleDuringSerialization).toBe(false)
      expect(JSON.parse(readFileSync(readyFile, 'utf8'))).toEqual({
        pid: 123,
        proxyBaseUrl: 'http://127.0.0.1:9999/v1',
        ready: true,
      })
      expect(statSync(readyFile).mode & 0o777).toBe(0o600)
    } finally {
      rmSync(runRoot, { force: true, recursive: true })
    }
  })

  it('preserves existing content when exclusive publication collides', () => {
    const runRoot = mkdtempSync(path.join(tmpdir(), 'moldy-egress-publication-'))
    const readyFile = path.join(runRoot, 'ready.json')
    const original = '{"ready":false}\n'
    writeFileSync(readyFile, original, { mode: 0o600 })

    try {
      let failure
      try {
        writeExclusiveJson(readyFile, { ready: true })
      } catch (error) {
        failure = error
      }

      expect(failure).toMatchObject({ code: 'EEXIST' })
      expect(readFileSync(readyFile, 'utf8')).toBe(original)
      expect(readdirSync(runRoot)).toEqual(['ready.json'])
    } finally {
      rmSync(runRoot, { force: true, recursive: true })
    }
  })
})
