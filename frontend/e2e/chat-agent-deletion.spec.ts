import { API_BASE, apiDeleteOk, apiGetJson, expect, isRecord, test } from './fixtures'
import { sendMessageForRun, setupLangGraphV3Agent } from './langgraph-v3-helpers'

// B-08 (agent archive lifecycle): deleting an agent during a durable run archives it and
// requests the run's cancellation instead of refusing; the run and its conversation are
// kept, the run settles as canceled, and a later delete of the archived agent still works.
test('archives an agent deleted during a durable run and cancels the run', async ({
  page,
  request,
}) => {
  test.setTimeout(120_000)
  const setup = await setupLangGraphV3Agent(request)
  try {
    // Given: a real browser starts the scripted streaming runtime.
    await page.goto(`/agents/${setup.parentAgentId}/conversations/${setup.conversationId}`)
    const runId = await sendMessageForRun(page, setup.conversationId, 'E2E_VISUAL_SLOW_STREAM')
    // When: the owner deletes the agent before the run finishes.
    const archived = await request.delete(`${API_BASE}/api/agents/${setup.parentAgentId}`, {
      headers: setup.csrfHeaders,
    })
    // Then: the agent is archived, the run is kept and settles as canceled.
    expect(archived.status()).toBe(204)
    await expect
      .poll(
        async () => {
          const run = await apiGetJson(
            request,
            `${API_BASE}/api/conversations/${setup.conversationId}/runs/${runId}`,
          )
          return isRecord(run) && typeof run.status === 'string' ? run.status : null
        },
        { timeout: 45_000, intervals: [500, 1000, 2000] },
      )
      .toBe('canceled')
  } finally {
    // Deleting the already archived agent again must succeed (it used to 500 on PostgreSQL).
    await apiDeleteOk(request, `${API_BASE}/api/agents/${setup.parentAgentId}`, setup.csrfHeaders)
    await apiDeleteOk(request, `${API_BASE}/api/agents/${setup.childAgentId}`, setup.csrfHeaders)
  }
})
