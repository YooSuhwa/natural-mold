import { describe, expect, it } from 'vitest'

import { redactSensitiveRecord, redactSensitiveText } from '../sensitive-display'

describe('sensitive-display redaction', () => {
  it('redacts bearer tokens embedded in prose', () => {
    expect(redactSensitiveText('Authorization failed for Bearer eyJhbGciOi.secret')).toBe(
      'Authorization failed for Bearer <redacted>',
    )
  })

  it('keeps token usage metrics while redacting secret-like keys', () => {
    expect(
      redactSensitiveRecord({
        api_key: 'raw-secret-value',
        usage_metadata: { prompt_tokens: 12, total_tokens: 42 },
      }),
    ).toEqual({
      api_key: '<redacted>',
      usage_metadata: { prompt_tokens: 12, total_tokens: 42 },
    })
  })
})
