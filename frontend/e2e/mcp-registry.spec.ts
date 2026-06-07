import { test, expect } from './fixtures'

// E2E: MCP server wizard "From Registry" flow.
//
// Mocks:
// - GET /api/mcp-server-types (registry catalog)
// - POST /api/mcp-servers/from-registry (one-click create)
// - GET/POST /api/mcp-servers (list + auto-create-from-registry sink)
// - POST /api/mcp-servers/<id>/discover (tool discovery)

const NOW = new Date().toISOString()

const REGISTRY = [
  {
    key: 'github',
    display_name: 'GitHub',
    description: 'Manage repos, issues, PRs through natural language',
    icon_id: 'code',
    transport: 'streamable_http',
    url: 'https://api.githubcopilot.com/mcp/',
    command: null,
    args: null,
    env_vars: { GITHUB_PERSONAL_ACCESS_TOKEN: '${credential.token}' },
    credential_definition_key: 'http_bearer',
    documentation_url: 'https://docs.github.com/mcp',
  },
  {
    key: 'linear',
    display_name: 'Linear',
    description: 'Issue tracking and project management',
    icon_id: 'wrench',
    transport: 'sse',
    url: 'https://mcp.linear.app/sse',
    command: null,
    args: null,
    env_vars: {},
    credential_definition_key: null,
    documentation_url: null,
  },
]

const FAKE_CRED_TYPES = [
  {
    key: 'http_bearer',
    display_name: 'HTTP Bearer',
    icon_id: 'key',
    documentation_url: null,
    category: 'http',
    extends: [],
    properties: [],
    has_test: false,
    has_oauth: false,
  },
]

const FAKE_CREDENTIALS = [
  {
    id: 'cred-bearer-1',
    user_id: 'user-1',
    definition_key: 'http_bearer',
    name: 'GitHub PAT',
    field_keys: ['token'],
    is_shared: false,
    status: 'active',
    key_id: 'k1',
    last_used_at: null,
    last_tested_at: null,
    last_test_result: null,
    created_at: NOW,
    updated_at: NOW,
  },
]

const NEW_SERVER = {
  id: 'srv-github-1',
  user_id: 'user-1',
  name: 'GitHub',
  description: 'Manage repos, issues, PRs through natural language',
  transport: 'streamable_http',
  url: 'https://api.githubcopilot.com/mcp/',
  command: null,
  args: [],
  env_vars: {},
  headers: {},
  credential_id: 'cred-bearer-1',
  status: 'connected',
  last_pinged_at: null,
  last_tool_count: 1,
  last_error: null,
  created_at: NOW,
  updated_at: NOW,
}

test.describe('MCP server wizard — From Registry', () => {
  test('GitHub card → auto-fill → credential picker → save → table row', async ({ page }) => {
    let servers: Array<Record<string, unknown>> = []

    await page.route('**/api/mcp-server-types', (route) => route.fulfill({ json: REGISTRY }))
    await page.route('**/api/credential-types', (route) => route.fulfill({ json: FAKE_CRED_TYPES }))
    await page.route('**/api/credentials', (route) => route.fulfill({ json: FAKE_CREDENTIALS }))
    await page.route(/\/api\/mcp-servers$/, (route) => route.fulfill({ json: servers }))
    await page.route('**/api/mcp-servers/probe', (route) =>
      route.fulfill({
        json: {
          success: true,
          status: 'connected',
          tools: [
            {
              name: 'list_repos',
              description: 'List repositories',
              input_schema: {},
            },
          ],
          error: null,
        },
      }),
    )
    await page.route('**/api/mcp-servers/from-registry', (route) => {
      servers = [NEW_SERVER]
      return route.fulfill({ status: 201, json: NEW_SERVER })
    })
    await page.route(`**/api/mcp-servers/srv-github-1/discover`, (route) =>
      route.fulfill({
        json: {
          success: true,
          status: 'connected',
          tools: [
            {
              id: 'tool-1',
              server_id: 'srv-github-1',
              name: 'list_repos',
              description: 'List repositories',
              input_schema: {},
              enabled: true,
              created_at: NOW,
              updated_at: NOW,
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

    // Step 1: Registry quick-start is visible.
    await expect(page.getByText('빠른 시작')).toBeVisible()

    // Click GitHub card.
    await page.getByTestId('registry-card-github').click()

    // Auto-filled name should be visible.
    await expect(page.getByLabel('이름')).toHaveValue('GitHub')

    await page.getByRole('button', { name: '인증으로 계속 →' }).click()

    // Step 2: Auth — credential filter limits to http_bearer.
    await expect(page.getByText('http_bearer 타입 자격증명으로 필터링되었습니다.')).toBeVisible()
    // Pick the bearer credential
    await page.getByRole('combobox').click()
    await page.getByRole('option', { name: /GitHub PAT/i }).click()

    await page.getByRole('button', { name: '도구로 계속 →' }).click()

    // Step 3: Discover + save
    await expect(page.getByText('1개 도구 발견됨')).toBeVisible()
    await expect(page.getByRole('checkbox')).toHaveCount(0)
    await page.getByRole('button', { name: '서버 저장' }).click()

    // After close, the table should show the new server (mocked list).
    await expect(page.getByText('GitHub').first()).toBeVisible()
  })
})
