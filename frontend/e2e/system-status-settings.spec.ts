import { test, expect } from './fixtures'

// System status (operator screen, plan §11 / D49): every component of the install profile is
// checked against the real backend, and the page shows what the API graded. The database
// must be up; the E2E stack may or may not run a job worker.
const API =
  process.env.E2E_API_BASE_URL ?? `http://localhost:${process.env.E2E_BACKEND_PORT ?? '8001'}`

test.describe('System status (super_user)', () => {
  test.skip(process.env.PW_SKIP_BACKEND === '1', 'Requires the FastAPI backend')

  test('the sidebar opens the status with every component graded', async ({ page, request }) => {
    test.setTimeout(60_000)
    const api = await request.get(`${API}/api/system/status`)
    expect(api.ok()).toBeTruthy()
    const status = (await api.json()) as {
      profile: string
      components: { key: string; found: string; verdict: string }[]
    }
    expect(status.components).toHaveLength(7)

    await page.goto('/settings')
    await page.getByRole('link', { name: '시스템 구성' }).click()
    await expect(page).toHaveURL(/\/settings\/system-status$/)
    await expect(page.getByTestId('system-status-summary')).toBeVisible({ timeout: 20_000 })

    const database = page.locator('[data-component="database"]')
    await expect(database.locator('[data-verdict]')).toHaveAttribute('data-verdict', 'ok')
    for (const component of status.components) {
      await expect(page.locator(`[data-component="${component.key}"]`)).toBeVisible()
    }
    const worker = status.components.find((c) => c.key === 'worker')
    await expect(page.locator('[data-component="worker"] [data-verdict]')).toHaveAttribute(
      'data-verdict',
      worker?.verdict ?? 'error',
    )
  })
})
