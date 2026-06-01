import { test, expect } from './fixtures'

// E2E: Model connection test surfaces.
//
// Backend interactions are mocked via Playwright `page.route` so this spec can
// run with or without the FastAPI backend up. Coverage:
// 1. /models row "Test" → mock 200 response → success card → Show Details →
//    Curl tab → Copy button.
// 2. ModelAddDialog Custom ID tab → fill form → Test → mock 401 → 인증 실패
//    label visible.

const NOW = new Date().toISOString()

const FAKE_CRED_TYPES = [
  {
    key: 'openai',
    display_name: 'OpenAI',
    icon_id: 'openai',
    documentation_url: null,
    category: 'llm',
    extends: [],
    properties: [],
    has_test: true,
    has_oauth: false,
  },
]

const FAKE_CREDENTIALS = [
  {
    id: 'cred-openai-1',
    user_id: 'user-1',
    definition_key: 'openai',
    name: 'My OpenAI',
    field_keys: ['api_key'],
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

const FAKE_MODEL = {
  id: 'model-1',
  provider: 'openai',
  model_name: 'gpt-4o-mini',
  display_name: 'GPT-4o mini',
  base_url: null,
  is_default: false,
  cost_per_input_token: 0.00000015,
  cost_per_output_token: 0.0000006,
  context_window: 128000,
  max_output_tokens: 16384,
  input_modalities: ['text'],
  output_modalities: ['text'],
  supports_vision: false,
  supports_function_calling: true,
  supports_reasoning: false,
  source: 'litellm',
  agent_count: 0,
  created_at: NOW,
}

const SUCCESS_RESPONSE = {
  success: true,
  response: 'Hello! Connection works.',
  latency_ms: 423,
  tokens_in: 12,
  tokens_out: 8,
  estimated_cost_usd: 0.0000054,
  error: null,
  raw_request: {
    url: 'https://api.openai.com/v1/chat/completions',
    method: 'POST',
    headers: {
      Authorization: 'Bearer sk-***MASKED***',
      'Content-Type': 'application/json',
    },
    body: { model: 'gpt-4o-mini', messages: [] },
  },
  raw_response: {
    status_code: 200,
    headers: {},
    body: { id: 'cmpl-x', choices: [] },
  },
  curl_command:
    "curl -X POST https://api.openai.com/v1/chat/completions -H 'Authorization: Bearer sk-***MASKED***' -d '{}'",
}

const AUTH_ERROR_RESPONSE = {
  success: false,
  response: null,
  latency_ms: 88,
  tokens_in: null,
  tokens_out: null,
  estimated_cost_usd: null,
  error: {
    kind: 'auth',
    message: 'Invalid API key supplied',
    raw: 'AuthenticationError: Incorrect API key',
  },
  raw_request: {
    url: 'https://api.openai.com/v1/chat/completions',
    method: 'POST',
    headers: { Authorization: 'Bearer sk-***MASKED***' },
    body: { model: 'gpt-x-preview', messages: [] },
  },
  raw_response: {
    status_code: 401,
    headers: {},
    body: { error: 'invalid_api_key' },
  },
  curl_command: null,
}

test.describe('Model connection test', () => {
  test.beforeEach(async ({ page }) => {
    await page.route('**/api/credential-types', (route) => route.fulfill({ json: FAKE_CRED_TYPES }))
    await page.route('**/api/credentials', (route) => route.fulfill({ json: FAKE_CREDENTIALS }))
  })

  test('row Test → success card → Show Details → Curl tab → Copy', async ({ page }) => {
    await page.route(/\/api\/models(\?.*)?$/, (route) => route.fulfill({ json: [FAKE_MODEL] }))
    await page.route('**/api/models/model-1/test**', (route) =>
      route.fulfill({ json: SUCCESS_RESPONSE }),
    )

    // Grant clipboard so the Copy button doesn't fail in the headless browser.
    await page.context().grantPermissions(['clipboard-read', 'clipboard-write'])

    await page.goto('/models')

    await expect(page.getByRole('heading', { name: '모델' })).toBeVisible()
    await expect(page.getByText('GPT-4o mini')).toBeVisible()

    await page.getByRole('button', { name: 'GPT-4o mini 테스트' }).click()

    await expect(page.getByRole('heading', { name: 'GPT-4o mini 테스트' })).toBeVisible()

    // Success card
    await expect(page.getByText(/연결 성공/)).toBeVisible()
    await expect(page.getByText(/Hello! Connection works/)).toBeVisible()
    await expect(page.getByText(/423 ms/)).toBeVisible()

    // Show details → Curl tab → Copy
    await page.getByTestId('toggle-details').click()
    await page.getByRole('tab', { name: /curl/i }).click()
    await page.getByTestId('copy-curl').click()
    await expect(page.getByText('클립보드에 복사했습니다')).toBeVisible()
  })

  test('Custom ID tab → mock 401 → 인증 실패', async ({ page }) => {
    await page.route(/\/api\/models(\?.*)?$/, (route) => route.fulfill({ json: [] }))
    await page.route('**/api/models/test-preview', (route) =>
      route.fulfill({ json: AUTH_ERROR_RESPONSE }),
    )

    await page.goto('/models')

    await page
      .getByRole('button', { name: /새 모델|모델 추가/ })
      .first()
      .click()
    await page.getByRole('tab', { name: '사용자 지정 ID' }).click()

    // Fill required fields. Provider defaults to "openai" so we just set ID.
    await page.getByLabel('모델 ID').fill('gpt-x-preview')

    await page.getByTestId('custom-test-button').click()

    // Error card with 인증 실패 label
    await expect(page.getByText(/인증 실패/)).toBeVisible()
    await expect(page.getByText(/Invalid API key supplied/)).toBeVisible()
  })
})
