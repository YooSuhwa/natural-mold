import type { APIRequestContext } from '@playwright/test'
import { API_BASE, apiGetJson, expect } from './fixtures'

export async function waitForThreadStateText(
  request: APIRequestContext,
  conversationId: string,
  text: string,
): Promise<void> {
  const encodedConversationId = encodeURIComponent(conversationId)
  await expect
    .poll(
      async () => {
        const state = await apiGetJson(
          request,
          `${API_BASE}/api/conversations/${encodedConversationId}/langgraph/threads/${encodedConversationId}/state`,
        )
        return JSON.stringify(state)?.includes(text) ?? false
      },
      { timeout: 45_000, intervals: [500, 1000, 2000] },
    )
    .toBe(true)
}
