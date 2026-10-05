import { API_BASE, apiDeleteOk, expect, test } from './fixtures'
import { setupLangGraphV3Agent, waitForRunStatus } from './langgraph-v3-helpers'
import { retireSeededRun } from './helpers/seeded-run-cleanup'
import { z } from 'zod'

const runSchema = z.object({ id: z.string(), status: z.string() })
const activeRunSchema = z.object({ active_run: runSchema.nullable() })
const pageSchema = z.object({
  items: z.array(activeRunSchema.extend({ id: z.string() })),
})

test('P1 exposes active conversation run state through list, active-run, and messages APIs', async ({
  request,
}) => {
  const setup = await setupLangGraphV3Agent(request)
  const agentId = setup.parentAgentId
  const csrfHeaders = setup.csrfHeaders
  let seeded: { conversationId: string; runId: string } | undefined
  try {
    const conversationId = setup.conversationId

    const seedRes = await request.post(`${API_BASE}/api/e2e/conversations/${conversationId}/runs`, {
      headers: csrfHeaders,
      data: {
        status: 'running',
        source: 'chat',
        input_preview: 'P1 active run contract',
      },
    })
    expect(seedRes.ok()).toBeTruthy()
    const seededRun = runSchema.parse(await seedRes.json())
    expect(seededRun.status).toBe('running')
    seeded = { conversationId, runId: seededRun.id }

    const pageRes = await request.get(`${API_BASE}/api/agents/${agentId}/conversations/page`)
    expect(pageRes.ok()).toBeTruthy()
    const page = pageSchema.parse(await pageRes.json())
    const item = page.items.find((conversation) => conversation.id === conversationId)
    expect(item?.active_run?.id).toBe(seededRun.id)
    expect(item?.active_run?.status).toBe('running')

    const activeRes = await request.get(
      `${API_BASE}/api/conversations/${conversationId}/runs/active`,
    )
    expect(activeRes.ok()).toBeTruthy()
    const activeRun = runSchema.parse(await activeRes.json())
    expect(activeRun.id).toBe(seededRun.id)
    expect(activeRun.status).toBe('running')

    const messagesRes = await request.get(
      `${API_BASE}/api/conversations/${conversationId}/messages`,
    )
    expect(messagesRes.ok()).toBeTruthy()
    const messages = activeRunSchema.parse(await messagesRes.json())
    expect(messages.active_run?.id).toBe(seededRun.id)
    expect(messages.active_run?.status).toBe('running')

    const forbiddenRes = await request.get(
      `${API_BASE}/api/conversations/00000000-0000-0000-0000-000000000099/runs/active`,
    )
    expect(forbiddenRes.status()).toBe(404)
  } finally {
    if (seeded) {
      await retireSeededRun(request, {
        url: `${API_BASE}/api/e2e/conversations/${seeded.conversationId}`,
        runId: seeded.runId,
        csrfHeaders,
      })
      await waitForRunStatus(request, seeded.conversationId, seeded.runId, 'stale')
    }
    await apiDeleteOk(request, `${API_BASE}/api/agents/${agentId}`, csrfHeaders)
    await apiDeleteOk(request, `${API_BASE}/api/agents/${setup.childAgentId}`, csrfHeaders)
  }
})
