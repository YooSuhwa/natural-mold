import { describe, expect, it } from 'vitest'

import {
  acceptedRunStart,
  commandMethodFromRequest,
  MAX_RUN_START_RESPONSE_BYTES,
  parseConversationRunStartUrl,
  parseRunStartResponseBody,
} from './e2e-run-start-response'

const conversationId = 'conversation-123'
const acceptedBody = {
  type: 'success',
  result: {
    status: 'accepted',
    conversation_id: conversationId,
    thread_id: conversationId,
    run_id: 'run-123',
  },
}
const acceptedResponse = {
  ok: true,
  requestConversationId: conversationId,
  runIdHeader: 'run-123',
  body: acceptedBody,
}

describe('acceptedRunStart', () => {
  it('returns matching accepted conversation and run ids', () => {
    expect(acceptedRunStart(acceptedResponse)).toEqual({
      conversationId,
      runId: 'run-123',
    })
  })

  it('rejects an HTTP-success protocol error', () => {
    expect(() =>
      acceptedRunStart({
        ...acceptedResponse,
        body: { type: 'error', error: { code: 'MULTITASK_REJECTED' } },
      }),
    ).toThrow('run.start command did not return an accepted success response')
  })

  it('rejects a non-success HTTP response', () => {
    expect(() => acceptedRunStart({ ...acceptedResponse, ok: false })).toThrow(
      'run.start command did not succeed',
    )
  })

  it('rejects a non-record response body', () => {
    expect(() => acceptedRunStart({ ...acceptedResponse, body: [] })).toThrow(
      'run.start command did not return an accepted success response',
    )
  })

  it('rejects a response whose conversation and thread ids differ', () => {
    expect(() =>
      acceptedRunStart({
        ...acceptedResponse,
        body: {
          ...acceptedBody,
          result: { ...acceptedBody.result, thread_id: 'other-conversation' },
        },
      }),
    ).toThrow('run.start command returned inconsistent conversation and thread ids')
  })

  it.each(['conversation_id', 'thread_id', 'run_id'] as const)(
    'rejects a blank %s in the accepted response body',
    (field) => {
      expect(() =>
        acceptedRunStart({
          ...acceptedResponse,
          body: {
            ...acceptedBody,
            result: { ...acceptedBody.result, [field]: ' ' },
          },
        }),
      ).toThrow(`run.start command did not include result.${field}`)
    },
  )

  it('rejects a request and response conversation id mismatch', () => {
    expect(() =>
      acceptedRunStart({ ...acceptedResponse, requestConversationId: 'other-conversation' }),
    ).toThrow('run.start command returned an inconsistent request conversation id')
  })

  it.each([null, ' '])('rejects a missing or blank run id header', (runIdHeader) => {
    expect(() => acceptedRunStart({ ...acceptedResponse, runIdHeader })).toThrow(
      'run.start command did not include X-Run-Id',
    )
  })

  it('rejects a header and body run id mismatch', () => {
    expect(() => acceptedRunStart({ ...acceptedResponse, runIdHeader: 'run-456' })).toThrow(
      'run.start command returned inconsistent run ids',
    )
  })
})

describe('generic run.start request parsing', () => {
  const commandPath = `/api/conversations/${conversationId}/langgraph/threads/${conversationId}/commands`

  it('rejects malformed command JSON without throwing', () => {
    expect(commandMethodFromRequest('POST', `http://api.test${commandPath}`, '{')).toBeNull()
  })

  it('returns the conversation id only for the exact API command path', () => {
    expect(parseConversationRunStartUrl(`http://api.test${commandPath}`, 'http://api.test')).toBe(
      conversationId,
    )
  })

  it('rejects the same path from a different origin', () => {
    expect(
      parseConversationRunStartUrl(`http://other.test${commandPath}`, 'http://api.test'),
    ).toBeNull()
  })

  it('rejects a non-command path', () => {
    expect(
      parseConversationRunStartUrl(
        `http://api.test/api/conversations/${conversationId}/langgraph/threads/${conversationId}/stream/events`,
        'http://api.test',
      ),
    ).toBeNull()
  })

  it('rejects a command URL whose conversation and thread ids differ', () => {
    expect(
      parseConversationRunStartUrl(
        `http://api.test/api/conversations/${conversationId}/langgraph/threads/other-conversation/commands`,
        'http://api.test',
      ),
    ).toBeNull()
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
