import { test, expect } from './fixtures'
import type { APIRequestContext } from '@playwright/test'

// Audit trail (/settings/audit), real backend: an auditable action (creating an
// agent emits `agent.create`) shows up in the personal audit log. The action
// filter is an exact match, so filtering to `agent.create` narrows the feed,
// then this agent's row is found by its unique target-name snapshot.
const API = process.env.E2E_API_BASE_URL ?? `http://localhost:${process.env.E2E_BACKEND_PORT ?? '8001'}`
const EMAIL = process.env.E2E_USER_EMAIL ?? process.env.E2E_EMAIL ?? 'playwright-e2e@moldy.dev'
const PASSWORD =
  process.env.E2E_USER_PASSWORD ?? process.env.E2E_PASSWORD ?? 'correct horse battery staple 42'

async function login(request: APIRequestContext): Promise<Record<string, string>> {
  const res = await request.post(`${API}/api/auth/login`, { data: { email: EMAIL, password: PASSWORD } })
  expect(res.ok()).toBeTruthy()
  return { 'X-CSRF-Token': (await res.json()).csrf_token as string }
}

test.describe('Audit trail', () => {
  test.skip(process.env.PW_SKIP_BACKEND === '1', 'Requires the FastAPI backend')

  let csrf: Record<string, string>
  let agentId: string
  const agentName = `E2E Audit Agent ${Date.now()}`

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
        data: { name: agentName, system_prompt: 'x', model_id: scripted.id },
      })
    ).json()) as { id: string }
    agentId = agent.id
  })

  test.afterAll(async ({ request }) => {
    if (agentId) await request.delete(`${API}/api/agents/${agentId}`, { headers: csrf })
  })

  test('a created agent surfaces an agent.create event in the personal log', async ({ page }) => {
    test.setTimeout(60_000)
    await page.goto('/settings/audit')

    // Filter the feed to agent.create, then locate this agent's event row.
    await page.getByLabel('기능', { exact: true }).fill('agent.create')
    await page.getByRole('button', { name: '필터 적용' }).click()

    // Generous timeout: under parallel load the backend's fixed 4-conn
    // checkpointer pool serializes, slowing the audit-events query.
    const row = page.getByRole('button').filter({ hasText: agentName })
    await expect(row).toBeVisible({ timeout: 20_000 })
    await expect(row).toContainText('agent.create')
  })
})
