// @vitest-environment node
import { createServer } from 'node:http'
import { request } from '@playwright/test'
import { afterEach, describe, expect, it } from 'vitest'
import { http, passthrough } from 'msw'

import { waitForAgentRunsBeforeDelete } from '../e2e/helpers/agent-cleanup'
import { server as mockServer } from './setup'

const disposals: (() => Promise<void>)[] = []

afterEach(async () => {
  for (const dispose of disposals.splice(0).reverse()) await dispose()
})

async function serve(responses: readonly { status: number; body: unknown }[]) {
  mockServer.use(
    http.get(/^http:\/\/127\.0\.0\.1:\d+\/api\/agents\/test-agent\/conversations$/, () =>
      passthrough(),
    ),
  )
  const paths: string[] = []
  const server = createServer((req, res) => {
    paths.push(req.url ?? '')
    const response = responses[Math.min(paths.length - 1, responses.length - 1)]
    if (!response) throw new Error('No response fixture')
    res.writeHead(response.status, { 'Content-Type': 'application/json' })
    res.end(JSON.stringify(response.body))
  })
  await new Promise<void>((resolve) => server.listen(0, '127.0.0.1', resolve))
  disposals.push(
    () =>
      new Promise<void>((resolve, reject) =>
        server.close((error) => (error ? reject(error) : resolve())),
      ),
  )
  const address = server.address()
  if (address === null || typeof address === 'string') throw new Error('No listening address')
  const api = await request.newContext()
  disposals.push(() => api.dispose())
  return { api, paths, agentUrl: `http://127.0.0.1:${address.port}/api/agents/test-agent` }
}

describe('agent E2E cleanup', () => {
  it('waits for every active run to finish before returning to the delete caller', async () => {
    // Given: the server advances through the durable active states.
    const fixture = await serve([
      ...['queued', 'running', 'canceling'].map((status) => ({
        status: 200,
        body: [{ active_run: { id: 'run-a', status } }],
      })),
      { status: 200, body: [{ active_run: null }] },
    ])
    // When: cleanup waits before its DELETE.
    await waitForAgentRunsBeforeDelete(fixture.api, fixture.agentUrl)
    // Then: accepting a run or rendering text cannot satisfy the barrier.
    expect(fixture.paths).toEqual(Array(4).fill('/api/agents/test-agent/conversations'))
  })

  it('allows retained interrupted runs without resuming or canceling them', async () => {
    const fixture = await serve([
      { status: 200, body: [{ active_run: { id: 'paused', status: 'interrupted' } }] },
    ])
    await waitForAgentRunsBeforeDelete(fixture.api, fixture.agentUrl)
    expect(fixture.paths).toHaveLength(1)
  })

  it('allows an already absent agent', async () => {
    const fixture = await serve([{ status: 404, body: { detail: 'not found' } }])
    await waitForAgentRunsBeforeDelete(fixture.api, fixture.agentUrl)
    expect(fixture.paths).toHaveLength(1)
  })

  it('propagates a server failure instead of treating it as idle', async () => {
    const fixture = await serve([{ status: 500, body: { detail: 'fixture failure' } }])
    await expect(waitForAgentRunsBeforeDelete(fixture.api, fixture.agentUrl)).rejects.toThrow('500')
    expect(fixture.paths).toHaveLength(1)
  })

  it('rejects an unparseable conversation list', async () => {
    const fixture = await serve([{ status: 200, body: [{ active_run: { id: 'run-a' } }] }])
    await expect(waitForAgentRunsBeforeDelete(fixture.api, fixture.agentUrl)).rejects.toThrow()
    expect(fixture.paths).toHaveLength(1)
  })
})
