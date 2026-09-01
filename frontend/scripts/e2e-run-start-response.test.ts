import { describe, expect, it } from 'vitest'

import {
  acceptedRunId,
  commandMethodFromRequest,
  isConversationRunStartUrl,
  MAX_RUN_START_RESPONSE_BYTES,
  parseRunStartResponseBody,
} from './e2e-run-start-response'

const acceptedBody = {
  type: 'success',
  result: { status: 'accepted', run_id: 'run-123' },
}

describe('acceptedRunId', () => {
  it('returns the matching accepted run id', () => {
    expect(acceptedRunId({ ok: true, runIdHeader: 'run-123', body: acceptedBody })).toBe('run-123')
  })

  it('rejects an HTTP-success protocol error', () => {
    expect(() =>
      acceptedRunId({
        ok: true,
        runIdHeader: 'run-123',
        body: { type: 'error', error: { code: 'MULTITASK_REJECTED' } },
      }),
    ).toThrow('run.start command did not return an accepted success response')
  })

  it('rejects a non-success HTTP response', () => {
    expect(() => acceptedRunId({ ok: false, runIdHeader: 'run-123', body: acceptedBody })).toThrow(
      'run.start command did not succeed',
    )
  })

  it('rejects a non-record response body', () => {
    expect(() => acceptedRunId({ ok: true, runIdHeader: 'run-123', body: [] })).toThrow(
      'run.start command did not return an accepted success response',
    )
  })

  it('rejects a blank run id header', () => {
    expect(() => acceptedRunId({ ok: true, runIdHeader: ' ', body: acceptedBody })).toThrow(
      'run.start command did not include X-Run-Id',
    )
  })

  it('rejects a header and body run id mismatch', () => {
    expect(() => acceptedRunId({ ok: true, runIdHeader: 'run-456', body: acceptedBody })).toThrow(
      'run.start command returned inconsistent run ids',
    )
  })
})

describe('run.start command parsing', () => {
  const conversationId = 'conversation-123'
  const commandPath = `/api/conversations/${conversationId}/langgraph/threads/${conversationId}/commands`

  it('rejects malformed command JSON without throwing', () => {
    expect(commandMethodFromRequest('POST', `http://api.test${commandPath}`, '{')).toBeNull()
  })

  it('rejects the same path from a different origin', () => {
    expect(
      isConversationRunStartUrl(
        `http://other.test${commandPath}`,
        'http://api.test',
        conversationId,
      ),
    ).toBe(false)
  })

  it('rejects malformed and oversized response bodies', () => {
    expect(() => parseRunStartResponseBody(Buffer.from('{'))).toThrow(
      'run.start command did not return JSON',
    )
    expect(() => parseRunStartResponseBody(Buffer.alloc(MAX_RUN_START_RESPONSE_BYTES + 1))).toThrow(
      'run.start command response exceeded the supported size',
    )
  })
})
