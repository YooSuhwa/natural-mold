import { test, expect } from './fixtures'

// E2E: MCP server wizard happy path with mocked backend.

test.describe('MCP server wizard', () => {
  test('user can step through the wizard and import tools', async ({ page }) => {
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
    await page.route('**/api/mcp-server-types', (route) => route.fulfill({ json: [] }))
    await page.route(/\/api\/mcp-servers$/, (route) => {
      if (route.request().method() === 'POST') {
        servers = [baseServer]
        return route.fulfill({ status: 201, json: baseServer })
      }
      return route.fulfill({ json: servers })
    })
    await page.route('**/api/mcp-servers/probe', (route) =>
      route.fulfill({
        json: {
          success: true,
          status: 'connected',
          tools: [
            {
              name: 'echo',
              description: 'Echo input',
              input_schema: {},
            },
          ],
          error: null,
        },
      }),
    )
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
    await page
      .getByRole('button', { name: /새 MCP 서버|서버 추가/ })
      .first()
      .click()

    // Step 1: basics
    await page.getByLabel('이름').fill('Local MCP')
    await page.getByRole('textbox', { name: 'URL *' }).fill('https://example.com/mcp')
    await page.getByRole('button', { name: '인증으로 계속 →' }).click()

    // Step 2: auth — skip
    await expect(page.getByText('자격증명 보간')).toBeVisible()
    await page.getByRole('button', { name: '도구로 계속 →' }).click()

    // Step 3: discover + save
    await expect(page.getByText('1개 도구 발견됨')).toBeVisible()
    await expect(page.getByRole('checkbox')).toHaveCount(0)
    await page.getByRole('button', { name: '서버 저장' }).click()

    await expect(page.getByText('Local MCP').first()).toBeVisible()
  })
})
