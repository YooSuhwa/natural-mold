import { test, expect } from './fixtures'
import type { APIRequestContext } from '@playwright/test'

// Marketplace: the catalog renders the seeded skills, installing one creates an
// independent copy (installed_skill_id), and the "installed" tab reflects it.
// (The multi-step InstallWizard UI is a follow-up; install is driven via API.)
const API =
  process.env.E2E_API_BASE_URL ?? `http://localhost:${process.env.E2E_BACKEND_PORT ?? '8001'}`
const EMAIL = process.env.E2E_USER_EMAIL ?? process.env.E2E_EMAIL ?? 'playwright-e2e@moldy.dev'
const PASSWORD =
  process.env.E2E_USER_PASSWORD ?? process.env.E2E_PASSWORD ?? 'correct horse battery staple 42'
const ITEM_NAME = 'PPTX Presentation'

async function login(request: APIRequestContext): Promise<Record<string, string>> {
  const res = await request.post(`${API}/api/auth/login`, {
    data: { email: EMAIL, password: PASSWORD },
  })
  expect(res.ok()).toBeTruthy()
  return { 'X-CSRF-Token': (await res.json()).csrf_token as string }
}

test.describe('Marketplace', () => {
  test.skip(process.env.PW_SKIP_BACKEND === '1', 'Requires the FastAPI backend')

  test('browses the catalog and installs a skill into the account', async ({ page, request }) => {
    test.setTimeout(60_000)
    const csrf = await login(request)

    // 1. Catalog renders the seeded skill.
    await page.goto('/marketplace')
    await expect(page.getByText(ITEM_NAME).first()).toBeVisible()

    // 2. Install it (creates an independent skill copy).
    const items = (await (await request.get(`${API}/api/marketplace/items`)).json()) as {
      id: string
      name: string
    }[]
    const item = items.find((i) => i.name === ITEM_NAME)
    if (!item) throw new Error('seeded marketplace item should exist')
    const installRes = await request.post(`${API}/api/marketplace/items/${item.id}/install`, {
      headers: csrf,
      data: {},
    })
    expect(
      installRes.ok(),
      `install ${installRes.status()}: ${await installRes.text()}`,
    ).toBeTruthy()
    const installation = (await installRes.json()) as { installed_skill_id?: string }
    expect(installation.installed_skill_id).toBeTruthy()

    // 3. The installed tab reflects it.
    await page.goto('/marketplace')
    await page.getByRole('tab', { name: '설치됨' }).click()
    await expect(page.getByText(ITEM_NAME).first()).toBeVisible()
  })
})
