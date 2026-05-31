import { test as base, expect } from '@playwright/test'

type ErrorCollector = {
  console: string[]
  page: string[]
  network: string[]
}

const E2E_USER = {
  id: '00000000-0000-4000-8000-000000000001',
  email: process.env.E2E_EMAIL ?? 'playwright-e2e@moldy.dev',
  name: process.env.E2E_NAME ?? 'E2E User',
  is_super_user: true,
  is_active: true,
  created_at: '2026-01-01T00:00:00.000Z',
  last_login_at: null,
}

export const test = base.extend<{ authMock: void; errors: ErrorCollector }>({
  authMock: [
    async ({ page }, use) => {
      if (process.env.PW_SKIP_BACKEND === '1') {
        await page.route('**/api/auth/me', (route) => route.fulfill({ json: E2E_USER }))
      }
      await use()
    },
    { auto: true },
  ],
  errors: async ({ page }, use) => {
    const errors: ErrorCollector = { console: [], page: [], network: [] }

    page.on('console', (msg) => {
      if (msg.type() === 'error') {
        const text = msg.text()
        // Ignore known benign errors
        if (
          !text.includes('favicon') &&
          !text.includes('Download the React DevTools') &&
          !text.includes('React DevTools')
        ) {
          errors.console.push(text)
        }
      }
    })
    page.on('pageerror', (err) => errors.page.push(err.message))
    page.on('requestfailed', (req) => {
      const url = req.url()
      if (!url.includes('favicon')) {
        errors.network.push(`${req.method()} ${url}`)
      }
    })

    await use(errors)

    // Auto-verify: no JS exceptions after each test
    expect(errors.page, 'JS exceptions detected').toEqual([])
  },
})

export { expect }
