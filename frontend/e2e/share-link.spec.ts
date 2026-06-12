import { test, expect } from './fixtures'
import type { APIRequestContext } from '@playwright/test'

// Public share links: send a message, publish the conversation, then open the
// read-only /shared/{token} page in a fresh logged-OUT context (the headline
// "anyone can open without signing in" promise).
const API = process.env.E2E_API_BASE_URL ?? `http://localhost:${process.env.E2E_BACKEND_PORT ?? '8001'}`
const FRONTEND = process.env.E2E_BASE_URL ?? `http://localhost:${process.env.E2E_FRONTEND_PORT ?? '3000'}`
const EMAIL = process.env.E2E_USER_EMAIL ?? process.env.E2E_EMAIL ?? 'playwright-e2e@moldy.dev'
const PASSWORD =
  process.env.E2E_USER_PASSWORD ?? process.env.E2E_PASSWORD ?? 'correct horse battery staple 42'

async function login(request: APIRequestContext): Promise<Record<string, string>> {
  const res = await request.post(`${API}/api/auth/login`, { data: { email: EMAIL, password: PASSWORD } })
  expect(res.ok()).toBeTruthy()
  return { 'X-CSRF-Token': (await res.json()).csrf_token as string }
}

test.describe('Public share link', () => {
  test.skip(process.env.PW_SKIP_BACKEND === '1', 'Requires the FastAPI backend')

  let csrf: Record<string, string>
  let agentId: string
  let conversationId: string

  test.beforeAll(async ({ request }) => {
    csrf = await login(request)
    const models = (await (await request.get(`${API}/api/models`)).json()) as {
      id: string
      provider: string
    }[]
    const scripted = models.find((m) => m.provider === 'e2e_scripted')!
    const agent = (await (
      await request.post(`${API}/api/agents`, {
        headers: csrf,
        data: { name: 'E2E Share Agent', system_prompt: 'x', model_id: scripted.id },
      })
    ).json()) as { id: string }
    agentId = agent.id
    const conv = (await (
      await request.post(`${API}/api/agents/${agentId}/conversations`, {
        headers: csrf,
        data: { title: 'E2E Share Conversation' },
      })
    ).json()) as { id: string }
    conversationId = conv.id
  })

  test.afterAll(async ({ request }) => {
    if (agentId) await request.delete(`${API}/api/agents/${agentId}`, { headers: csrf })
  })

  test('publishes a conversation and serves it read-only to a logged-out visitor', async ({
    page,
    request,
    browser,
  }) => {
    test.setTimeout(90_000)
    // Re-login on the test-scoped request context so its CSRF header matches
    // this context's moldy_csrf cookie (beforeAll's login ran on a different,
    // worker-scoped context).
    const reqCsrf = await login(request)
    const message = `E2E shared message ${Date.now()}`

    // 1. Put a real message into the conversation (scripted model).
    await page.goto(`/agents/${agentId}/conversations/${conversationId}`)
    await page.getByPlaceholder('메시지 입력...').fill(message)
    await page.getByRole('button', { name: /전송/ }).click()
    await expect(page.getByText(message)).toBeVisible()
    // Let the run commit at least one assistant message into the snapshot.
    await expect
      .poll(
        async () => {
          const data = (await (
            await request.get(`${API}/api/conversations/${conversationId}/messages`)
          ).json()) as { messages?: { role: string; content: string }[] }
          return (data.messages ?? []).filter((m) => m.role === 'assistant' && m.content).length
        },
        { timeout: 60_000, intervals: [1500] },
      )
      .toBeGreaterThan(0)

    // 2. Publish the conversation.
    const shareRes = await request.post(`${API}/api/conversations/${conversationId}/share`, {
      headers: reqCsrf,
    })
    const shareBody = await shareRes.text()
    expect(shareRes.ok(), `share create ${shareRes.status()}: ${shareBody}`).toBeTruthy()
    const shareToken = (JSON.parse(shareBody) as { share_token: string }).share_token
    expect(shareToken).toBeTruthy()

    // 3. Open the public page with NO auth (fresh context).
    const anon = await browser.newContext({ storageState: { cookies: [], origins: [] } })
    try {
      const pub = await anon.newPage()
      await pub.goto(`${FRONTEND}/shared/${shareToken}`)
      await expect(pub.getByText(message)).toBeVisible({ timeout: 15_000 })
    } finally {
      await anon.close()
    }

    // 4. Revoking the link makes it inaccessible.
    const revoke = await request.delete(`${API}/api/conversations/${conversationId}/share`, {
      headers: reqCsrf,
    })
    expect(revoke.ok()).toBeTruthy()
    const gone = await request.get(`${API}/api/shares/${shareToken}`)
    expect(gone.status()).toBe(404)
  })
})
