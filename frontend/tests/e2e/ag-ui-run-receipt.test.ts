import { describe, expect, it, vi } from 'vitest'
import { readAgUiAcceptance, readAgUiRunInput } from '../../e2e/helpers/ag-ui-run-receipt'

vi.mock('../../e2e/fixtures', () => ({
  API_BASE: 'http://localhost:8101',
  isRecord: (value: unknown) =>
    value !== null && typeof value === 'object' && !Array.isArray(value),
}))

const url = 'http://localhost:8101/api/conversations/thread-1/ag-ui/runs'
const input = {
  protocolVersion: '1.0',
  threadId: 'thread-1',
  runId: 'sdk-opaque-run',
  messages: [{ id: 'm1', role: 'user', content: 'fixture' }],
  context: [],
  tools: [],
}
const request = (address = url, body: unknown = input, method = 'POST') => ({
  url: () => address,
  method: () => method,
  postData: () => JSON.stringify(body),
})
const response = (headers: Record<string, string>) => ({
  request: () => request(),
  status: () => 200,
  allHeaders: async () => ({ 'content-type': 'text/event-stream; charset=utf-8', ...headers }),
})

describe('AG-UI browser acceptance', () => {
  it('matches only the configured origin, endpoint, thread, and explicit protocol version', () => {
    expect(readAgUiRunInput(request(), 'thread-1')?.runId).toBe(input.runId)
    expect(readAgUiRunInput(request(url.replace('localhost:8101', 'other.test')))).toBeNull()
    expect(readAgUiRunInput(request(url.replace('ag-ui/runs', 'langgraph/commands')))).toBeNull()
    expect(readAgUiRunInput(request(url, input, 'GET'))).toBeNull()
    expect(readAgUiRunInput(request(), 'thread-2')).toBeNull()
    expect(readAgUiRunInput(request(url, { ...input, threadId: 'thread-2' }))).toBeNull()
    expect(readAgUiRunInput(request(url, { ...input, protocolVersion: undefined }))).toBeNull()
    expect(readAgUiRunInput(request(url, { ...input, messages: [{}] }))).toBeNull()
  })

  it('distinguishes a physical run from an accepted pending input', async () => {
    const identity = { 'X-Run-Id': input.runId }
    await expect(
      readAgUiAcceptance(response({ ...identity, 'X-Moldy-Run-Id': 'worker-run' })),
    ).resolves.toMatchObject({ publicRunId: input.runId, physicalRunId: 'worker-run', queue: null })
    await expect(
      readAgUiAcceptance(
        response({
          ...identity,
          'X-Moldy-Input-Id': 'input-2',
          'X-Moldy-Input-Status': 'pending',
          'X-Moldy-Input-Position': '2',
          'X-Moldy-Input-Revision': '1',
        }),
      ),
    ).resolves.toMatchObject({
      publicRunId: input.runId,
      physicalRunId: null,
      queue: { inputId: 'input-2' },
    })
  })

  it('rejects an absent or mismatched public ID and a JSON command acknowledgement', async () => {
    await expect(readAgUiAcceptance(response({}))).rejects.toThrow('public run ID')
    await expect(readAgUiAcceptance(response({ 'X-Run-Id': 'other' }))).rejects.toThrow(
      'public run ID',
    )
    await expect(readAgUiAcceptance(response({ 'X-Run-Id': input.runId }))).rejects.toThrow(
      'queued input',
    )
    await expect(
      readAgUiAcceptance(response({ 'content-type': 'application/json' })),
    ).rejects.toThrow('event stream')
  })
})
