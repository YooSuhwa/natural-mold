import { test as base, expect } from '@playwright/test'

type ErrorCollector = {
  console: string[]
  page: string[]
  network: string[]
}

export const test = base.extend<{ errors: ErrorCollector }>({
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
