import { test, expect } from './fixtures'
import type { APIRequestContext, Page } from '@playwright/test'
import fs from 'node:fs/promises'
import path from 'node:path'

const BACKEND_PORT = process.env.E2E_BACKEND_PORT ?? '8001'
const API_BASE = process.env.E2E_API_BASE_URL ?? `http://localhost:${BACKEND_PORT}`
const E2E_EMAIL = process.env.E2E_USER_EMAIL ?? process.env.E2E_EMAIL ?? 'playwright-e2e@moldy.dev'
const E2E_PASSWORD =
  process.env.E2E_USER_PASSWORD ?? process.env.E2E_PASSWORD ?? 'correct horse battery staple 42'

async function loginApi(request: APIRequestContext): Promise<Record<string, string>> {
  const res = await request.post(`${API_BASE}/api/auth/login`, {
    data: { email: E2E_EMAIL, password: E2E_PASSWORD },
  })
  expect(res.ok()).toBeTruthy()
  const body = (await res.json()) as { csrf_token: string }
  return { 'X-CSRF-Token': body.csrf_token }
}

async function listConversationIds(
  request: APIRequestContext,
  agentId: string,
): Promise<string[]> {
  const res = await request.get(`${API_BASE}/api/agents/${agentId}/conversations`)
  expect(res.ok()).toBeTruthy()
  const conversations = (await res.json()) as { id: string }[]
  return conversations.map((conversation) => conversation.id)
}

async function listConversations(
  request: APIRequestContext,
  agentId: string,
): Promise<{ id: string; title: string }[]> {
  const res = await request.get(`${API_BASE}/api/agents/${agentId}/conversations`)
  expect(res.ok()).toBeTruthy()
  return (await res.json()) as { id: string; title: string }[]
}

async function capture(page: Page, name: string): Promise<void> {
  const outputDir = path.resolve(
    process.cwd(),
    '..',
    'output',
    'e2e-captures',
    '20260607-draft-conversation',
  )
  await fs.mkdir(outputDir, { recursive: true })
  await page.screenshot({
    path: path.join(outputDir, name),
    fullPage: true,
  })
}

test.describe('Draft conversation lifecycle', () => {
  test.skip(process.env.PW_SKIP_BACKEND === '1', 'Requires the FastAPI backend')

  let agentId: string
  let conversationId: string
  let csrfHeaders: Record<string, string>

  test.beforeAll(async ({ request }) => {
    csrfHeaders = await loginApi(request)

    const modelsRes = await request.get(`${API_BASE}/api/models`)
    expect(modelsRes.ok()).toBeTruthy()
    const models = (await modelsRes.json()) as { id: string }[]
    expect(models.length).toBeGreaterThan(0)

    const agentRes = await request.post(`${API_BASE}/api/agents`, {
      headers: csrfHeaders,
      data: {
        name: 'E2E Draft Conversation Agent',
        system_prompt: 'You are a draft conversation E2E test agent.',
        model_id: models[0].id,
      },
    })
    expect(agentRes.ok()).toBeTruthy()
    const agent = (await agentRes.json()) as { id: string }
    agentId = agent.id

    const conversationRes = await request.post(
      `${API_BASE}/api/agents/${agentId}/conversations`,
      {
        headers: csrfHeaders,
        data: { title: 'Existing E2E Conversation' },
      },
    )
    expect(conversationRes.ok()).toBeTruthy()
    const conversation = (await conversationRes.json()) as { id: string }
    conversationId = conversation.id
  })

  test.afterAll(async ({ request }) => {
    if (agentId) {
      await request.delete(`${API_BASE}/api/agents/${agentId}`, {
        headers: csrfHeaders ?? (await loginApi(request)),
      })
    }
  })

  test('clicking 새 대화 shows a draft row without creating a DB conversation', async ({
    page,
    request,
    errors,
  }) => {
    const createConversationPosts: string[] = []
    page.on('request', (req) => {
      const url = req.url()
      if (
        req.method() === 'POST' &&
        url.includes(`/api/agents/${agentId}/conversations`) &&
        !url.endsWith('/start')
      ) {
        createConversationPosts.push(url)
      }
    })

    const beforeIds = await listConversationIds(request, agentId)
    expect(beforeIds).toContain(conversationId)

    await page.goto(`/agents/${agentId}/conversations/${conversationId}`)
    await page.waitForLoadState('domcontentloaded')
    await expect(
      page.getByRole('main').getByRole('heading', { name: 'E2E Draft Conversation Agent' }).first(),
    ).toBeVisible()

    await page.getByRole('button', { name: '새 대화' }).first().click()
    await page.waitForURL(`**/agents/${agentId}/conversations/new`, { timeout: 10_000 })
    await expect(page.getByRole('link', { name: '새 대화' }).first()).toHaveAttribute(
      'href',
      `/agents/${agentId}/conversations/new`,
    )

    const afterIds = await listConversationIds(request, agentId)
    expect(afterIds).toEqual(beforeIds)
    expect(createConversationPosts).toEqual([])

    await capture(page, 'draft-conversation-no-db-write.png')

    expect(errors.console).toEqual([])
    expect(errors.network).toEqual([])
  })

  test('draft route survives visiting another page and returning with browser history', async ({
    page,
    request,
    errors,
  }) => {
    const beforeIds = await listConversationIds(request, agentId)

    await page.goto(`/agents/${agentId}/conversations/${conversationId}`)
    await page.waitForLoadState('domcontentloaded')
    await page.getByRole('button', { name: '새 대화' }).first().click()
    await page.waitForURL(`**/agents/${agentId}/conversations/new`, { timeout: 10_000 })

    await page.goto(`/agents/${agentId}/settings`)
    await expect(page.getByRole('button', { name: '저장' })).toBeVisible()
    await page.goBack()
    await page.waitForURL(`**/agents/${agentId}/conversations/new`, { timeout: 10_000 })
    await expect(page.getByRole('link', { name: '새 대화' }).first()).toBeVisible()
    await expect(page.getByPlaceholder('메시지 입력...')).toBeVisible()

    const afterIds = await listConversationIds(request, agentId)
    expect(afterIds).toEqual(beforeIds)

    expect(errors.console).toEqual([])
    expect(errors.network).toEqual([])
  })

  test('first message from draft creates one conversation and moves to its URL', async ({
    page,
    request,
    errors,
  }) => {
    test.setTimeout(120_000)

    const before = await listConversations(request, agentId)
    const beforeIds = before.map((conversation) => conversation.id)
    const startRequests: string[] = []
    const directCreateRequests: string[] = []

    page.on('request', (req) => {
      const url = req.url()
      if (req.method() !== 'POST' || !url.includes(`/api/agents/${agentId}/conversations`)) {
        return
      }
      if (url.endsWith('/start')) {
        startRequests.push(url)
      } else {
        directCreateRequests.push(url)
      }
    })

    await page.goto(`/agents/${agentId}/conversations/${conversationId}`)
    await page.waitForLoadState('domcontentloaded')
    await page.getByRole('button', { name: '새 대화' }).first().click()
    await page.waitForURL(`**/agents/${agentId}/conversations/new`, { timeout: 10_000 })

    const firstMessage = 'Draft E2E first message'
    await page.getByPlaceholder('메시지 입력...').fill(firstMessage)
    await page.getByRole('button', { name: /전송/ }).click()
    await page.waitForURL(
      new RegExp(`/agents/${agentId}/conversations/(?!new$)[0-9a-f-]+$`),
      { timeout: 90_000 },
    )

    const createdConversationId = page.url().split('/').pop()
    expect(createdConversationId).toBeTruthy()
    expect(startRequests).toHaveLength(1)
    expect(directCreateRequests).toEqual([])

    await expect
      .poll(async () => listConversations(request, agentId), { timeout: 10_000 })
      .toEqual(
        expect.arrayContaining([
          expect.objectContaining({
            id: createdConversationId,
            title: firstMessage,
          }),
        ]),
      )

    const after = await listConversations(request, agentId)
    const afterIds = after.map((conversation) => conversation.id)
    expect(afterIds).toHaveLength(beforeIds.length + 1)
    expect(afterIds).toContain(createdConversationId)
    expect(beforeIds).not.toContain(createdConversationId)

    await capture(page, 'draft-first-message-created-conversation.png')

    expect(errors.console).toEqual([])
    expect(errors.network).toEqual([])
  })
})
