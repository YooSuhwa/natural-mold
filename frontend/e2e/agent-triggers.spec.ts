import { test, expect } from './fixtures'
import type { APIRequestContext } from '@playwright/test'

// Schedule triggers: create an interval trigger via the API, then verify the
// settings → triggers tab renders it (name + "매 N분" schedule summary).
const API = process.env.E2E_API_BASE_URL ?? `http://localhost:${process.env.E2E_BACKEND_PORT ?? '8001'}`
const EMAIL = process.env.E2E_USER_EMAIL ?? process.env.E2E_EMAIL ?? 'playwright-e2e@moldy.dev'
const PASSWORD =
  process.env.E2E_USER_PASSWORD ?? process.env.E2E_PASSWORD ?? 'correct horse battery staple 42'

async function login(request: APIRequestContext): Promise<Record<string, string>> {
  const res = await request.post(`${API}/api/auth/login`, { data: { email: EMAIL, password: PASSWORD } })
  expect(res.ok()).toBeTruthy()
  return { 'X-CSRF-Token': (await res.json()).csrf_token as string }
}

test.describe('Agent schedule triggers', () => {
  test.skip(process.env.PW_SKIP_BACKEND === '1', 'Requires the FastAPI backend')

  let csrf: Record<string, string>
  let agentId: string
  const triggerName = `E2E Interval Trigger ${Date.now()}`

  test.beforeAll(async ({ request }) => {
    csrf = await login(request)
    const models = (await (await request.get(`${API}/api/models`)).json()) as {
      id: string
      provider: string
    }[]
    // Scheduled triggers require a "fixed"-identity agent; its credential is
    // resolved from the model's default (the seeded LiteLLM model is bound).
    const litellmModel = models.find((m) => m.provider === 'openai_compatible')!
    const agent = (await (
      await request.post(`${API}/api/agents`, {
        headers: csrf,
        data: {
          name: 'E2E Trigger Agent',
          system_prompt: 'x',
          model_id: litellmModel.id,
          identity_mode: 'fixed',
        },
      })
    ).json()) as { id: string }
    agentId = agent.id

    const res = await request.post(`${API}/api/agents/${agentId}/triggers`, {
      headers: csrf,
      data: {
        name: triggerName,
        trigger_type: 'interval',
        schedule_config: { interval_minutes: 30 },
        input_message: 'Run the scheduled task.',
      },
    })
    expect(res.status(), await res.text()).toBe(201)
  })

  test.afterAll(async ({ request }) => {
    if (agentId) await request.delete(`${API}/api/agents/${agentId}`, { headers: csrf })
  })

  test('a created interval trigger renders in the settings triggers tab', async ({ page }) => {
    await page.goto(`/agents/${agentId}/settings`)
    await page.getByRole('tab', { name: '스케줄' }).click()

    // The interval trigger renders with its "매 N분" schedule summary.
    await expect(page.getByText('매 30분').first()).toBeVisible()
  })
})
