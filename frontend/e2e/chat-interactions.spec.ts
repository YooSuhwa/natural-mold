import { test, expect } from './fixtures'
import type { APIRequestContext, Page, Request } from '@playwright/test'
import { z } from 'zod'
import { isSeededScriptedModel } from './helpers/scripted-model'
import { branchStage, observeBranch } from './helpers/branch-observation'
import { observeChatTurns } from './helpers/turn-observation'

// Detailed coverage of the core chat interactions against the live backend +
// the keyless scripted model: branching by regenerate, branching by editing a
// user message, and thumbs feedback. assistant-ui reveals the action bar on
// hover; opacity-0 controls are still clickable.
const API =
  process.env.E2E_API_BASE_URL ?? `http://localhost:${process.env.E2E_BACKEND_PORT ?? '8001'}`
const EMAIL = process.env.E2E_USER_EMAIL ?? process.env.E2E_EMAIL ?? 'playwright-e2e@moldy.dev'
const PASSWORD =
  process.env.E2E_USER_PASSWORD ?? process.env.E2E_PASSWORD ?? 'correct horse battery staple 42'

const branchTest = test.extend<{ branch: ReturnType<typeof observeBranch> }>({
  branch: async ({ page }, run, info) => {
    const branch = observeBranch(page, API)
    await run(branch)
    await info.attach('chat-branch-observation', {
      body: JSON.stringify(await branch.finish()),
      contentType: 'application/json',
    })
  },
})

async function login(request: APIRequestContext): Promise<Record<string, string>> {
  const res = await request.post(`${API}/api/auth/login`, {
    data: { email: EMAIL, password: PASSWORD },
  })
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
        (await (await request.get(`${API}/api/conversations/${convId}/runs/active`)).json()) ===
        null,
      { timeout: 60_000, intervals: [1000] },
    )
    .toBe(true)
}

async function fillComposer(page: Page, text: string): Promise<void> {
  const composer = page.locator('textarea[data-moldy-composer-input="true"]:visible')
  await expect(composer).toHaveCount(1)
  await expect(composer).toBeVisible()
  await composer.fill(text)
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
    const scripted = models.find(isSeededScriptedModel)
    if (!scripted) throw new Error('e2e_scripted model should be seeded')
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
    await fillComposer(page, text)
    await page.getByRole('button', { name: /전송/ }).click()
    await expect(page.getByText(text).first()).toBeVisible()
    await expect
      .poll(() => assistantCount(request, conv.id), { timeout: 60_000 })
      .toBeGreaterThan(0)
    await waitRunIdle(request, conv.id)
    return conv.id
  }

  branchTest(
    'regenerating an assistant reply forks a sibling branch and the picker navigates',
    async ({ page, request, branch }) => {
      test.setTimeout(90_000)
      await startTurn(page, request, 'First question for regenerate')
      branch.reached(branchStage.turnIdle)

      // Regenerate → a second sibling forks; the <n/2> branch picker appears.
      // (The messages API returns only the active branch — siblings show as
      // branch_total=2 metadata, not as extra messages — so assert on the picker.)
      await page.getByRole('button', { name: '재생성' }).first().click()
      branch.reached(branchStage.regenerateClicked)
      await expect(page.getByRole('button', { name: '이전 분기' })).toBeVisible({ timeout: 60_000 })
      branch.reached(branchStage.pickerVisible)
      await expect(page.getByText('2/2').first()).toBeVisible()
      branch.reached(branchStage.latestBranchVisible)

      // Navigate to the earlier sibling.
      await page.getByRole('button', { name: '이전 분기' }).click()
      branch.reached(branchStage.previousClicked)
      await expect(page.getByText('1/2').first()).toBeVisible()
    },
  )

  branchTest('editing a user message forks a new branch', async ({ page, request, branch }) => {
    test.setTimeout(90_000)
    await startTurn(page, request, 'Original user message')
    branch.reached(branchStage.turnIdle)

    await page.getByRole('button', { name: '편집' }).first().click()
    branch.reached(branchStage.editClicked)
    // The inline edit composer is the (autofocused) textarea with no placeholder.
    await page.locator('textarea:not([placeholder])').fill('Edited user message')
    branch.reached(branchStage.editorFilled)
    await page.getByRole('button', { name: '저장', exact: true }).click()
    branch.reached(branchStage.saveClicked)

    // The edited message forks a sibling branch (picker on the user message).
    await expect(page.getByRole('button', { name: '이전 분기' })).toBeVisible({ timeout: 60_000 })
    branch.reached(branchStage.pickerVisible)
    await expect(page.getByText('2/2').first()).toBeVisible()
    branch.reached(branchStage.latestBranchVisible)
  })

  test('thumbs-up feedback submits and sticks on the assistant message', async ({
    page,
    request,
  }) => {
    test.setTimeout(90_000)
    const origin = new URL(API).origin
    const isFeedbackWrite = (req: Request) => {
      const url = new URL(req.url())
      return (
        url.origin === origin &&
        url.pathname.startsWith('/api/messages/') &&
        url.pathname.endsWith('/feedback') &&
        ['POST', 'DELETE'].includes(req.method())
      )
    }
    const writes: Request[] = []
    page.on('request', (req) => {
      if (isFeedbackWrite(req)) writes.push(req)
    })
    const conversationId = await startTurn(page, request, 'Please rate this answer')
    const reply = page.locator('[data-moldy-message-role="assistant"]')
    await expect(reply, 'one assistant reply in the new conversation').toHaveCount(1)
    const positive = reply.getByRole('button', { name: '도움이 됨', exact: true })
    await expect(positive, 'the reply offers positive feedback').toBeVisible({ timeout: 10_000 })
    await expect(positive).toBeEnabled({ timeout: 10_000 })
    expect(writes).toHaveLength(0)

    const [response] = await Promise.all([
      page.waitForResponse((res) => isFeedbackWrite(res.request()), { timeout: 15_000 }),
      positive.click({ timeout: 10_000 }),
    ])
    expect(response.request().method()).toBe('POST')
    expect(response.ok(), 'the feedback write was accepted').toBe(true)
    expect(response.request().postDataJSON()).toMatchObject({
      conversation_id: conversationId,
      rating: 'up',
    })

    // Resolve the authoritative ID after the first click so an extra read cannot mask a race.
    const messages = await request.get(`${API}/api/conversations/${conversationId}/messages`)
    expect(messages.ok()).toBe(true)
    const saved = z
      .object({
        messages: z.array(
          z.object({
            id: z.string().uuid(),
            role: z.string(),
            content: z.string(),
            feedback: z.object({ rating: z.string() }).nullable(),
          }),
        ),
      })
      .parse(await messages.json())
    const answers = saved.messages.filter((row) => row.role === 'assistant' && row.content)
    expect(answers).toHaveLength(1)
    const answer = answers[0]
    expect(new URL(response.url()).pathname).toBe(`/api/messages/${answer.id}/feedback`)
    await expect(reply).toContainText(answer.content)
    expect(answer.feedback).toEqual({ rating: 'up' })

    const mine = await request.get(`${API}/api/conversations/${conversationId}/feedback`)
    expect(mine.ok()).toBe(true)
    expect(await mine.json()).toEqual([
      expect.objectContaining({
        message_id: answer.id,
        conversation_id: conversationId,
        rating: 'up',
      }),
    ])
    expect(writes.map((req) => req.method())).toEqual(['POST'])
  })

  test('a multi-turn conversation keeps both exchanges', async ({ page, request }) => {
    test.setTimeout(90_000)
    const first = 'First turn message'
    const second = 'Second turn message'
    const answer = 'E2E scripted document model is ready.'
    const expectedContents = [first, answer, second, answer]
    const observed = observeChatTurns(page, API)
    let conversationId: string | undefined
    try {
      conversationId = await startTurn(page, request, first)
      observed.reached('first-saved')
      const users = page.locator('[data-moldy-message-role="user"]')
      const assistants = page.locator('[data-moldy-message-role="assistant"]')
      await expect(users.getByText(first, { exact: true })).toBeVisible()
      const endpoint = API + '/api/conversations/' + conversationId + '/messages'
      const readTurns = async () => {
        const response = await request.get(endpoint)
        expect(response.ok()).toBeTruthy()
        const { messages } = (await response.json()) as {
          messages: { id: string; role: string; content: string }[]
        }
        return messages.filter((m) => m.role === 'user' || m.role === 'assistant')
      }
      const original = (await readTurns()).map((m) => m.id)
      expect(original).toHaveLength(2)

      await fillComposer(page, second)
      // A completed server run can still be draining in the browser. Wait for
      // normal Send; the preemption confirmation also contains the word 전송.
      const form = page.locator('form[data-moldy-thread-composer]:visible')
      await form.locator('button[type="submit"]').click()
      observed.reached('second-sent')
      await expect(users.getByText(second, { exact: true })).toBeVisible()
      observed.reached('second-visible')

      await expect
        .poll(
          async () => {
            const turns = await readTurns()
            return {
              orderedRoles: turns.map((m) => m.role),
              matchesContents: turns.every((m, index) => m.content === expectedContents[index]),
              keepsFirstTurn: original.every((id, index) => turns[index]?.id === id),
              uniqueIds: new Set(turns.map((m) => m.id)).size,
            }
          },
          { timeout: 60_000 },
        )
        .toEqual({
          orderedRoles: ['user', 'assistant', 'user', 'assistant'],
          matchesContents: true,
          keepsFirstTurn: true,
          uniqueIds: 4,
        })
      observed.reached('both-saved')
      await expect(assistants.getByText(answer, { exact: true })).toHaveCount(2)
      await expect(users.getByText(first, { exact: true })).toBeVisible()
      await expect(users.getByText(second, { exact: true })).toBeVisible()
      await page.reload()
      await expect(users.getByText(first, { exact: true })).toBeVisible()
      await expect(users.getByText(second, { exact: true })).toBeVisible()
      await expect(assistants.getByText(answer, { exact: true })).toHaveCount(2)
      observed.reached('both-restored')
    } finally {
      if (conversationId) await observed.attach(test.info(), request, conversationId)
      observed.dispose()
    }
  })
})
