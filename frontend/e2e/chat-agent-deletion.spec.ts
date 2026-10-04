import { API_BASE, apiDeleteOk, apiGetJson, expect, isRecord, test } from './fixtures'
import { sendMessageForRun, setupLangGraphV3Agent, waitForRunStatus } from './langgraph-v3-helpers'

test('rejects agent deletion during a durable run and deletes after completion', async ({
  page,
  request,
}) => {
  test.setTimeout(120_000)
  const setup = await setupLangGraphV3Agent(request)
  try {
    // Given: a real browser starts the scripted streaming runtime.
    await page.goto(`/agents/${setup.parentAgentId}/conversations/${setup.conversationId}`)
    const runId = await sendMessageForRun(page, setup.conversationId, 'E2E_VISUAL_SLOW_STREAM')
    // When: the owner attempts cascade deletion before durable finalization.
    const rejected = await request.delete(`${API_BASE}/api/agents/${setup.parentAgentId}`, {
      headers: setup.csrfHeaders,
    })
    // Then: reject safely, preserve the run, and allow it to finish normally.
    expect(rejected.status()).toBe(409)
    const retained = await apiGetJson(
      request,
      `${API_BASE}/api/conversations/${setup.conversationId}/runs/${runId}`,
    )
    expect(isRecord(retained) && retained.id).toBe(runId)
    await waitForRunStatus(request, setup.conversationId, runId, 'completed')
  } finally {
    await apiDeleteOk(request, `${API_BASE}/api/agents/${setup.parentAgentId}`, setup.csrfHeaders)
    await apiDeleteOk(request, `${API_BASE}/api/agents/${setup.childAgentId}`, setup.csrfHeaders)
  }
})
