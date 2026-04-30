import { test, expect } from './fixtures'

// E2E: Tools catalog → create flow.
// Mocks backend so the test runs without uvicorn.

const TOOL_DEFS = [
  {
    key: 'http_request',
    display_name: 'HTTP Request',
    description: 'Send arbitrary HTTP requests with optional auth.',
    icon_id: 'http_request',
    category: 'http',
    parameters: [
      {
        name: 'url',
        display_name: 'URL',
        kind: 'string',
        required: true,
        options: [],
        type_options: {},
        display_options: {},
      },
    ],
    credential_definition_keys: [],
    requires_credential: false,
  },
]

test.describe('Tools catalog', () => {
  test('user can pick a tool from the catalog and create an instance', async ({ page }) => {
    let tools: Array<Record<string, unknown>> = []

    await page.route('**/api/tool-types', (route) => route.fulfill({ json: TOOL_DEFS }))
    await page.route('**/api/credentials', (route) => route.fulfill({ json: [] }))
    await page.route(/\/api\/tools(\?.*)?$/, (route) => {
      if (route.request().method() === 'POST') {
        const body = route.request().postDataJSON() as Record<string, unknown>
        const created = {
          id: 'tool-1',
          user_id: 'user-1',
          definition_key: body.definition_key,
          name: body.name,
          description: body.description ?? null,
          parameters: body.parameters ?? {},
          credential_id: body.credential_id ?? null,
          enabled: true,
          last_used_at: null,
          created_at: new Date().toISOString(),
          updated_at: new Date().toISOString(),
        }
        tools = [created]
        return route.fulfill({ status: 201, json: created })
      }
      return route.fulfill({ json: tools })
    })

    await page.goto('/tools')

    // Catalog tab is selected by default; pick HTTP Request
    await expect(page.getByText(/http request/i).first()).toBeVisible()
    await page.getByRole('button', { name: /http request/i }).first().click()

    // Dialog opened
    await expect(page.getByRole('heading', { name: /new http request/i })).toBeVisible()
    await page.getByLabel('Name').fill('Webhook')
    await page.getByLabel('URL').fill('https://example.com/hook')

    await page.getByRole('button', { name: /create tool/i }).click()
    await expect(page.getByText(/tool created/i)).toBeVisible()

    // Switched to manage tab — row appears
    await expect(page.getByText('Webhook')).toBeVisible()
  })
})
