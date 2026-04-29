import { test, expect } from './fixtures'

// E2E: MCP server wizard happy path with mocked backend.

test.describe('MCP server wizard', () => {
  test('user can step through the 4-step wizard and import tools', async ({ page }) => {
    let servers: Array<Record<string, unknown>> = []
    const baseServer = {
      id: 'server-1',
      user_id: 'user-1',
      name: 'Local MCP',
      description: null,
      transport: 'streamable_http',
      url: 'https://example.com/mcp',
      command: null,
      args: [],
      env_vars: {},
      headers: {},
      credential_id: null,
      status: 'unknown',
      last_pinged_at: null,
      last_tool_count: null,
      last_error: null,
      created_at: new Date().toISOString(),
      updated_at: new Date().toISOString(),
    }

    await page.route('**/api/credentials', (route) => route.fulfill({ json: [] }))
    await page.route(/\/api\/mcp-servers$/, (route) => {
      if (route.request().method() === 'POST') {
        servers = [baseServer]
        return route.fulfill({ status: 201, json: baseServer })
      }
      return route.fulfill({ json: servers })
    })
    await page.route('**/api/mcp-servers/server-1/discover', (route) =>
      route.fulfill({
        json: {
          success: true,
          status: 'connected',
          tools: [
            {
              id: 't1',
              server_id: 'server-1',
              name: 'echo',
              description: 'Echo input',
              input_schema: {},
              enabled: true,
              created_at: new Date().toISOString(),
              updated_at: new Date().toISOString(),
            },
          ],
          error: null,
        },
      }),
    )

    await page.goto('/mcp-servers')
    await page.getByRole('button', { name: /new mcp server/i }).first().click()

    // Step 1: basics
    await page.getByLabel('Name').fill('Local MCP')
    await page.getByLabel('URL').fill('https://example.com/mcp')
    await page.getByRole('button', { name: /next/i }).click()

    // Step 2: auth — skip
    await expect(page.getByText(/optionally bind a credential/i)).toBeVisible()
    await page.getByRole('button', { name: /next/i }).click()

    // Step 3: discover
    await expect(page.getByText(/we'll connect to the server/i)).toBeVisible()
    await page.getByRole('button', { name: /next/i }).click()

    // Step 4: confirm
    await expect(page.getByText(/1 tool imported/i)).toBeVisible()
    await page.getByRole('button', { name: /done/i }).click()
  })
})
