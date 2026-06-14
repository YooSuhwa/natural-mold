import { test, expect, API_BASE, apiDeleteOk, apiGetJson, isRecord } from './fixtures'
import type { APIRequestContext } from '@playwright/test'
import { setupLangGraphV3Agent } from './langgraph-v3-helpers'

interface ConversationRow {
  readonly id: string
}

function conversationRows(value: unknown): ConversationRow[] {
  if (!Array.isArray(value)) {
    throw new Error('conversation list did not return conversation rows')
  }
  return value.map((row) => {
    if (!isRecord(row) || typeof row.id !== 'string') {
      throw new Error('conversation list row did not include an id')
    }
    return { id: row.id }
  })
}

async function listConversationIds(request: APIRequestContext, agentId: string): Promise<string[]> {
  const rows = conversationRows(
    await apiGetJson(request, `${API_BASE}/api/agents/${agentId}/conversations`),
  )
  return rows.map((conversation) => conversation.id)
}

function findCreatedConversationId(
  beforeIds: readonly string[],
  afterIds: readonly string[],
): string {
  const before = new Set(beforeIds)
  const createdId = afterIds.find((id) => !before.has(id))
  if (!createdId) {
    throw new Error('conversation list did not include a newly created id')
  }
  return createdId
}

test.describe('LangGraph v3 draft conversation lifecycle', () => {
  test.skip(process.env.PW_SKIP_BACKEND === '1', 'Requires the FastAPI backend')
  test.skip(
    process.env.NEXT_PUBLIC_CHAT_RUNTIME !== 'langgraph_v3',
    'Requires NEXT_PUBLIC_CHAT_RUNTIME=langgraph_v3',
  )

  test('bootstraps a concrete SDK thread when entering the draft route', async ({
    page,
    request,
    errors,
  }) => {
    const setup = await setupLangGraphV3Agent(request)
    const createConversationPosts: string[] = []
    const startRequests: string[] = []
    page.on('request', (req) => {
      const url = req.url()
      if (
        req.method() !== 'POST' ||
        !url.includes(`/api/agents/${setup.parentAgentId}/conversations`)
      ) {
        return
      }
      if (url.endsWith('/start')) {
        startRequests.push(url)
        return
      }
      createConversationPosts.push(url)
    })

    try {
      const beforeIds = await listConversationIds(request, setup.parentAgentId)
      await page.goto(`/agents/${setup.parentAgentId}/conversations/${setup.conversationId}`)
      await page.getByRole('button', { name: '새 채팅', exact: true }).first().click()
      await page.waitForURL(`**/agents/${setup.parentAgentId}/conversations/new`, {
        timeout: 10_000,
      })

      await expect
        .poll(async () => listConversationIds(request, setup.parentAgentId), {
          timeout: 10_000,
        })
        .toHaveLength(beforeIds.length + 1)
      expect(createConversationPosts).toHaveLength(1)
      expect(startRequests).toEqual([])
      await expect(page.getByPlaceholder('메시지 입력...')).toBeVisible({ timeout: 20_000 })

      expect(errors.console).toEqual([])
      expect(errors.network).toEqual([])
    } finally {
      await apiDeleteOk(request, `${API_BASE}/api/agents/${setup.parentAgentId}`, setup.csrfHeaders)
      await apiDeleteOk(request, `${API_BASE}/api/agents/${setup.childAgentId}`, setup.csrfHeaders)
    }
  })

  test('creates a fresh SDK thread when re-entering the draft route for the same agent', async ({
    page,
    request,
    errors,
  }) => {
    const setup = await setupLangGraphV3Agent(request)
    const createConversationPosts: string[] = []
    const startRequests: string[] = []
    page.on('request', (req) => {
      const url = req.url()
      if (
        req.method() !== 'POST' ||
        !url.includes(`/api/agents/${setup.parentAgentId}/conversations`)
      ) {
        return
      }
      if (url.endsWith('/start')) {
        startRequests.push(url)
        return
      }
      createConversationPosts.push(url)
    })

    try {
      const beforeIds = await listConversationIds(request, setup.parentAgentId)
      await page.goto(`/agents/${setup.parentAgentId}/conversations/${setup.conversationId}`)
      await page.getByRole('button', { name: '새 채팅', exact: true }).first().click()
      await page.waitForURL(`**/agents/${setup.parentAgentId}/conversations/new`, {
        timeout: 10_000,
      })

      await expect
        .poll(async () => listConversationIds(request, setup.parentAgentId), {
          timeout: 10_000,
        })
        .toHaveLength(beforeIds.length + 1)
      const firstDraftIds = await listConversationIds(request, setup.parentAgentId)
      const firstCreatedId = findCreatedConversationId(beforeIds, firstDraftIds)

      await page.goto(`/agents/${setup.parentAgentId}/conversations/${firstCreatedId}`)
      await page.getByRole('button', { name: '새 채팅', exact: true }).first().click()
      await page.waitForURL(`**/agents/${setup.parentAgentId}/conversations/new`, {
        timeout: 10_000,
      })

      await expect
        .poll(async () => listConversationIds(request, setup.parentAgentId), {
          timeout: 10_000,
        })
        .toHaveLength(beforeIds.length + 2)
      expect(createConversationPosts).toHaveLength(2)
      expect(startRequests).toEqual([])
      await expect(page.getByPlaceholder('메시지 입력...')).toBeVisible({ timeout: 20_000 })

      expect(errors.console).toEqual([])
      expect(errors.network).toEqual([])
    } finally {
      await apiDeleteOk(request, `${API_BASE}/api/agents/${setup.parentAgentId}`, setup.csrfHeaders)
      await apiDeleteOk(request, `${API_BASE}/api/agents/${setup.childAgentId}`, setup.csrfHeaders)
    }
  })
})
