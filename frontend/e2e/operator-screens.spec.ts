import { test, expect } from './fixtures'
import type { APIRequestContext } from '@playwright/test'

// Operator-only screens (super_user). The seeded E2E user is a super_user, so
// both render. System LLM is seed-configured (LiteLLM), so its slots show as
// configured. System Credentials is exercised with a real create + delete
// through the shared catalog modal, verified via /api/system-credentials.
const API = process.env.E2E_API_BASE_URL ?? `http://localhost:${process.env.E2E_BACKEND_PORT ?? '8001'}`
const EMAIL = process.env.E2E_USER_EMAIL ?? process.env.E2E_EMAIL ?? 'playwright-e2e@moldy.dev'
const PASSWORD =
  process.env.E2E_USER_PASSWORD ?? process.env.E2E_PASSWORD ?? 'correct horse battery staple 42'

async function login(request: APIRequestContext): Promise<Record<string, string>> {
  const res = await request.post(`${API}/api/auth/login`, { data: { email: EMAIL, password: PASSWORD } })
  expect(res.ok()).toBeTruthy()
  return { 'X-CSRF-Token': (await res.json()).csrf_token as string }
}

type SysCred = { id: string; name: string; definition_key: string; is_system: boolean }

async function listSystemCredentials(request: APIRequestContext): Promise<SysCred[]> {
  const res = await request.get(`${API}/api/system-credentials`)
  expect(res.ok()).toBeTruthy()
  return (await res.json()) as SysCred[]
}

test.describe('Operator screens (super_user)', () => {
  test.skip(process.env.PW_SKIP_BACKEND === '1', 'Requires the FastAPI backend')

  let csrf: Record<string, string>
  const credName = `E2E System Key ${Date.now()}`

  test.beforeAll(async ({ request }) => {
    csrf = await login(request)
  })

  test.afterAll(async ({ request }) => {
    for (const c of await listSystemCredentials(request)) {
      if (c.name === credName) await request.delete(`${API}/api/system-credentials/${c.id}`, { headers: csrf })
    }
  })

  test('System LLM shows the seed-configured role slots', async ({ page, request }) => {
    await page.goto('/settings/system-llm')

    // Renders (not redirected away) — operator banner + the primary slot card.
    await expect(page.getByText('운영자 전용')).toBeVisible()
    await expect(page.getByText('텍스트 기본 모델')).toBeVisible()
    // The seed wired LiteLLM into text_primary/fallback → "설정됨" + cred name.
    await expect(page.getByText('설정됨').first()).toBeVisible()
    await expect(page.getByText('[e2e] LiteLLM').first()).toBeVisible()

    // Cross-check the data path.
    const settings = (await (await request.get(`${API}/api/system-llm-settings`)).json()) as {
      role: string
      configured: boolean
    }[]
    expect(settings.find((s) => s.role === 'text_primary')?.configured).toBe(true)
  })

  test('creates and deletes a system credential through the catalog modal', async ({ page, request }) => {
    test.setTimeout(60_000)
    await page.goto('/settings/system-credentials')

    // 1. Open the create modal and pick OpenAI from the catalog.
    await page.getByRole('button', { name: '시스템 자격증명 추가' }).first().click()
    const dialog = page.getByRole('dialog')
    await expect(dialog).toBeVisible()
    await dialog.getByText('OpenAI', { exact: true }).click()

    // 2. Name it uniquely and fill the only required (password) field.
    await dialog.getByLabel('이름').fill(credName)
    await dialog.locator('input[type="password"]').first().fill('sk-e2e-system-key')
    await dialog.getByRole('button', { name: '자격증명 저장' }).click()

    // 3. It persists as a system credential and renders in the list.
    await expect(page.getByText('자격증명이 저장되었습니다')).toBeVisible()
    const row = page.locator('li').filter({ hasText: credName })
    await expect(row).toBeVisible()
    await expect
      .poll(async () => {
        const c = (await listSystemCredentials(request)).find((x) => x.name === credName)
        return c ? `${c.definition_key}:${c.is_system}` : 'missing'
      }, { timeout: 15_000 })
      .toBe('openai:true')

    // 4. Delete it (native confirm) — the API drops it.
    page.once('dialog', (d) => d.accept())
    await row.getByRole('button').last().click()
    await expect
      .poll(async () => (await listSystemCredentials(request)).some((x) => x.name === credName), {
        timeout: 15_000,
      })
      .toBe(false)
  })
})
