import type { APIRequestContext } from '@playwright/test'

import { API_BASE, apiDeleteOk, apiGetJson, expect, isRecord, type CsrfHeaders } from '../fixtures'

/**
 * B-08: DELETE only archives an agent (it can be restored), so the agent keeps holding
 * its model. A fixture that also removes its model must archive, then purge the agent.
 */
export async function purgeAgent(
  request: APIRequestContext,
  csrfHeaders: CsrfHeaders,
  agentId: string,
): Promise<void> {
  await apiDeleteOk(request, `${API_BASE}/api/agents/${agentId}`, csrfHeaders)
  const archived = await apiGetJson(request, `${API_BASE}/api/agents/archived`)
  const row = Array.isArray(archived)
    ? archived.find((item) => isRecord(item) && item.agent_id === agentId)
    : undefined
  if (!isRecord(row) || typeof row.name !== 'string') {
    throw new Error(`archived agent ${agentId} is not listed`)
  }
  const purged = await request.post(`${API_BASE}/api/agents/${agentId}/purge`, {
    headers: csrfHeaders,
    data: { confirmation_name: row.name },
  })
  expect([200, 202]).toContain(purged.status())
}

/** Purge the agent, then delete the model once no agent row refers to it. */
export async function purgeAgentAndModel(
  request: APIRequestContext,
  csrfHeaders: CsrfHeaders,
  agentId: string,
  modelId: string,
): Promise<void> {
  await purgeAgent(request, csrfHeaders, agentId)
  await expect
    .poll(
      async () =>
        (
          await request.delete(`${API_BASE}/api/models/${modelId}`, { headers: csrfHeaders })
        ).status(),
      { timeout: 30_000, intervals: [500, 1000, 2000] },
    )
    .toBe(204)
}
