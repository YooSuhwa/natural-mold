import { test, expect } from './fixtures'
import type { APIRequestContext, Page } from '@playwright/test'

// Detailed coverage of the core chat interactions against the live backend +
// the keyless scripted model: branching by regenerate, branching by editing a
// user message, and thumbs feedback. assistant-ui reveals the action bar on
// hover; opacity-0 controls are still clickable.
const API = process.env.E2E_API_BASE_URL ?? `http://localhost:${process.env.E2E_BACKEND_PORT ?? '8001'}`
const EMAIL = process.env.E2E_USER_EMAIL ?? process.env.E2E_EMAIL ?? 'playwright-e2e@moldy.dev'
const PASSWORD =
  process.env.E2E_USER_PASSWORD ?? process.env.E2E_PASSWORD ?? 'correct horse battery staple 42'

async function login(request: APIRequestContext): Promise<Record<string, string>> {
  const res = await request.post(`${API}/api/auth/login`, { data: { email: EMAIL, password: PASSWORD } })
  expect(res.ok()).toBeTruthy()
  return { 'X-CSRF-Token': (await res.json()).csrf_token as string }
}

async function assistantCount(request: APIRequestContext, convId: string): Promise<number> {
  const d = (await (await request.get(`${API}/api/conversations/${convId}/messages`)).json()) as {
    messages?: { role: string; content: string }[]
  }
  return (d.messages ?? []).filter((m) => m.role === 'assistant' && m.content).length
}

/** Wait until no run is active — regenerate/edit require the prior run to finish. */
async function waitRunIdle(request: APIRequestContext, convId: string): Promise<void> {
  await expect
    .poll(
      async () =>
        (await (await request.get(`${API}/api/conversations/${convId}/runs/active`)).json()) === null,
      { timeout: 60_000, intervals: [1000] },
    )
    .toBe(true)
}

test.describe('Chat interactions', () => {
  test.skip(process.env.PW_SKIP_BACKEND === '1', 'Requires the FastAPI backend')

  let agentId: string

  test.beforeAll(async ({ request }) => {
    const csrf = await login(request)
    const models = (await (await request.get(`${API}/api/models`)).json()) as {
      id: string
      provider: string
    }[]
    const scripted = models.find((m) => m.provider === 'e2e_scripted')!
    const agent = (await (
      await request.post(`${API}/api/agents`, {
        headers: csrf,
        data: { name: 'E2E Chat Interactions Agent', system_prompt: 'x', model_id: scripted.id },
      })
    ).json()) as { id: string }
    agentId = agent.id
  })

  test.afterAll(async ({ request }) => {
    const csrf = await login(request)
    if (agentId) await request.delete(`${API}/api/agents/${agentId}`, { headers: csrf })
  })

  /** Fresh conversation + one completed scripted turn; returns the conversation id. */
  async function startTurn(page: Page, request: APIRequestContext, text: string): Promise<string> {
    const csrf = await login(request)
    const conv = (await (
      await request.post(`${API}/api/agents/${agentId}/conversations`, {
        headers: csrf,
        data: { title: 'E2E Chat Interactions' },
      })
    ).json()) as { id: string }
    await page.goto(`/agents/${agentId}/conversations/${conv.id}`)
    await page.getByPlaceholder('메시지 입력...').fill(text)
    await page.getByRole('button', { name: /전송/ }).click()
    await expect(page.getByText(text).first()).toBeVisible()
    await expect.poll(() => assistantCount(request, conv.id), { timeout: 60_000 }).toBeGreaterThan(0)
    await waitRunIdle(request, conv.id)
    return conv.id
  }

  test('regenerating an assistant reply forks a sibling branch and the picker navigates', async ({
    page,
    request,
  }) => {
    test.setTimeout(90_000)
    const convId = await startTurn(page, request, 'First question for regenerate')

    // Regenerate → a second sibling forks; the <n/2> branch picker appears.
    // (The messages API returns only the active branch — siblings show as
    // branch_total=2 metadata, not as extra messages — so assert on the picker.)
    await page.getByRole('button', { name: '재생성' }).first().click()
    await expect(page.getByRole('button', { name: '이전 분기' })).toBeVisible({ timeout: 60_000 })
    await expect(page.getByText('2/2').first()).toBeVisible()

    // Navigate to the earlier sibling.
    await page.getByRole('button', { name: '이전 분기' }).click()
    await expect(page.getByText('1/2').first()).toBeVisible()
  })

  test('editing a user message forks a new branch', async ({ page, request }) => {
    test.setTimeout(90_000)
    await startTurn(page, request, 'Original user message')

    await page.getByRole('button', { name: '편집' }).first().click()
    // The inline edit composer is the (autofocused) textarea with no placeholder.
    await page.locator('textarea:not([placeholder])').fill('Edited user message')
    await page.getByRole('button', { name: '저장', exact: true }).click()

    // The edited message forks a sibling branch (picker on the user message).
    await expect(page.getByRole('button', { name: '이전 분기' })).toBeVisible({ timeout: 60_000 })
    await expect(page.getByText('2/2').first()).toBeVisible()
  })

  test('thumbs-up feedback submits and sticks on the assistant message', async ({ page, request }) => {
    test.setTimeout(90_000)
    await startTurn(page, request, 'Please rate this answer')

    const feedbackPost = page.waitForResponse(
      (r) => /feedback/i.test(r.url()) && r.request().method() === 'POST',
    )
    await page.getByRole('button', { name: '도움이 됨' }).first().click()
    const res = await feedbackPost
    expect(res.ok()).toBeTruthy()
  })

  test('a multi-turn conversation keeps both exchanges', async ({ page, request }) => {
    test.setTimeout(90_000)
    const convId = await startTurn(page, request, 'First turn message')

    // Second turn into the same conversation.
    await page.getByPlaceholder('메시지 입력...').fill('Second turn message')
    await page.getByRole('button', { name: /전송/ }).click()
    await expect(page.getByText('Second turn message').first()).toBeVisible()
    await expect.poll(() => assistantCount(request, convId), { timeout: 60_000 }).toBeGreaterThanOrEqual(2)

    // Both user turns remain in the thread.
    await expect(page.getByText('First turn message').first()).toBeVisible()
    await expect(page.getByText('Second turn message').first()).toBeVisible()
  })
})
