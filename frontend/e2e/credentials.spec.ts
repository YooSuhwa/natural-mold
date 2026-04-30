import { test, expect } from './fixtures'

// E2E: Credentials page — create flow.
//
// Backend interactions are mocked via Playwright `page.route` so this spec can
// run with or without the FastAPI backend up. Coverage:
// 1. Page loads and shows the "New credential" CTA.
// 2. Clicking it opens the catalog modal.
// 3. Picking a definition reveals the dynamic form.
// 4. Filling required fields enables Save.
// 5. POST /api/credentials succeeds → row appears in DataTable.

const FAKE_DEFINITIONS = [
  {
    key: 'openai',
    display_name: 'OpenAI',
    icon_id: 'openai',
    documentation_url: null,
    category: 'llm',
    extends: [],
    properties: [
      {
        name: 'api_key',
        display_name: 'API key',
        kind: 'password',
        required: true,
        type_options: { password: true },
        display_options: {},
        options: [],
      },
    ],
    has_test: true,
    has_oauth: false,
  },
]

test.describe('Credentials page', () => {
  test('user can create a credential through the catalog modal', async ({ page }) => {
    let credentials: Array<Record<string, unknown>> = []

    await page.route('**/api/credential-types', (route) =>
      route.fulfill({ json: FAKE_DEFINITIONS }),
    )
    await page.route('**/api/credentials', (route) => {
      if (route.request().method() === 'POST') {
        const body = route.request().postDataJSON() as Record<string, unknown>
        const created = {
          id: 'cred-1',
          user_id: 'user-1',
          definition_key: body.definition_key,
          name: body.name,
          field_keys: ['api_key'],
          is_shared: false,
          status: 'active',
          key_id: 'k1',
          last_used_at: null,
          last_tested_at: null,
          last_test_result: null,
          created_at: new Date().toISOString(),
          updated_at: new Date().toISOString(),
        }
        credentials = [created]
        return route.fulfill({ status: 201, json: created })
      }
      return route.fulfill({ json: credentials })
    })

    await page.goto('/credentials')

    // Header CTA is present
    await expect(page.getByRole('button', { name: /new credential/i })).toBeVisible()

    // Open the modal
    await page.getByRole('button', { name: /new credential/i }).first().click()

    // Step 1: pick a definition (OpenAI)
    await expect(page.getByText(/pick a credential type/i)).toBeVisible()
    await page.getByRole('listitem', { name: /OpenAI/i }).first().click()

    // Step 2: form
    await expect(page.getByRole('heading', { name: /new openai credential/i })).toBeVisible()
    await page.getByLabel('Name').fill('Prod OpenAI')
    await page.getByLabel(/api key/i).fill('sk-test-1234')

    // Submit
    await page.getByRole('button', { name: /^save credential$/i }).click()

    // Toast and updated table
    await expect(page.getByText(/credential saved/i)).toBeVisible()
    await expect(page.getByText('Prod OpenAI')).toBeVisible()
  })
})
