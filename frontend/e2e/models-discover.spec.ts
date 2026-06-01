import { test, expect } from './fixtures'

// E2E: /models page — discover + custom-id flows.
//
// Backend interactions are mocked via Playwright `page.route` so this spec can
// run with or without the FastAPI backend up. Coverage:
// 1. /models renders an empty state and the "새 모델" CTA.
// 2. Discover tab — pick credential → discover → multi-select → save → row
//    appears in the catalog DataTable.
// 3. Custom ID tab — provider + model_name → save → row appears.

const FAKE_CRED_TYPES = [
  {
    key: 'openrouter',
    display_name: 'OpenRouter',
    icon_id: 'openrouter',
    documentation_url: null,
    category: 'llm',
    extends: [],
    properties: [],
    has_test: true,
    has_oauth: false,
  },
  {
    key: 'naver_search',
    display_name: 'Naver Search',
    icon_id: 'search',
    documentation_url: null,
    category: 'search',
    extends: [],
    properties: [],
    has_test: false,
    has_oauth: false,
  },
]

const FAKE_CREDENTIALS = [
  {
    id: 'cred-or-1',
    user_id: 'user-1',
    definition_key: 'openrouter',
    name: 'My OpenRouter',
    field_keys: ['api_key'],
    is_shared: false,
    status: 'active',
    key_id: 'k1',
    last_used_at: null,
    last_tested_at: null,
    last_test_result: null,
    created_at: new Date().toISOString(),
    updated_at: new Date().toISOString(),
  },
  // Non-LLM credential should be filtered out of the credential picker.
  {
    id: 'cred-naver-1',
    user_id: 'user-1',
    definition_key: 'naver_search',
    name: 'Naver Prod',
    field_keys: ['client_id', 'client_secret'],
    is_shared: false,
    status: 'active',
    key_id: 'k1',
    last_used_at: null,
    last_tested_at: null,
    last_test_result: null,
    created_at: new Date().toISOString(),
    updated_at: new Date().toISOString(),
  },
]

const DISCOVERED = [
  {
    model_name: 'anthropic/claude-3.5-sonnet',
    display_name: 'Claude 3.5 Sonnet',
    source: 'openrouter',
    provider: 'openrouter',
    context_window: 200000,
    max_output_tokens: 8192,
    cost_per_input_token: 0.000003,
    cost_per_output_token: 0.000015,
    input_modalities: ['text', 'image'],
    output_modalities: ['text'],
    supports_vision: true,
    supports_function_calling: true,
    supports_reasoning: false,
    already_registered: false,
  },
  {
    model_name: 'openai/gpt-4o',
    display_name: 'GPT-4o',
    source: 'openrouter',
    provider: 'openrouter',
    context_window: 128000,
    max_output_tokens: 4096,
    cost_per_input_token: 0.0000025,
    cost_per_output_token: 0.00001,
    input_modalities: ['text', 'image'],
    output_modalities: ['text'],
    supports_vision: true,
    supports_function_calling: true,
    supports_reasoning: false,
    already_registered: false,
  },
]

function makeModel(provider: string, modelName: string, displayName: string) {
  return {
    id: `${provider}-${modelName.replace(/[^a-z0-9]/gi, '-')}`,
    provider,
    model_name: modelName,
    display_name: displayName,
    base_url: null,
    is_default: false,
    cost_per_input_token: null,
    cost_per_output_token: null,
    context_window: null,
    max_output_tokens: null,
    input_modalities: null,
    output_modalities: null,
    supports_vision: null,
    supports_function_calling: null,
    supports_reasoning: null,
    source: 'manual',
    agent_count: 0,
    created_at: new Date().toISOString(),
  }
}

test.describe('Models page', () => {
  test.beforeEach(async ({ page }) => {
    await page.route('**/api/credential-types', (route) => route.fulfill({ json: FAKE_CRED_TYPES }))
    await page.route('**/api/credentials', (route) => route.fulfill({ json: FAKE_CREDENTIALS }))
  })

  test('user can discover models from a credential and save selections', async ({ page }) => {
    let models: Array<Record<string, unknown>> = []

    await page.route(/\/api\/models(\?.*)?$/, (route) => {
      if (route.request().method() === 'POST') {
        const body = route.request().postDataJSON() as Record<string, unknown>
        const created = makeModel(
          String(body.provider),
          String(body.model_name),
          String(body.display_name),
        )
        // The discovered fixtures include richer metadata — preserve `source`.
        if (body.source) created.source = String(body.source)
        models = [...models, created]
        return route.fulfill({ status: 201, json: created })
      }
      return route.fulfill({ json: models })
    })

    await page.route('**/api/credentials/cred-or-1/discover-models', (route) =>
      route.fulfill({ json: DISCOVERED }),
    )

    await page.goto('/models')

    await expect(page.getByRole('heading', { name: '모델' })).toBeVisible()
    await expect(page.getByText('아직 모델이 없어요')).toBeVisible()

    await page
      .getByRole('button', { name: /새 모델|모델 추가/ })
      .first()
      .click()
    await expect(page.getByRole('heading', { name: '모델 추가' })).toBeVisible()

    // Discover tab is the default. Open the credential picker.
    await page.getByRole('combobox').first().click()
    await page.getByRole('option', { name: /My OpenRouter/i }).click()

    await page.getByRole('button', { name: '탐색' }).click()

    // Both discovered models render.
    await expect(page.getByText('Claude 3.5 Sonnet').first()).toBeVisible()
    await expect(page.getByText('GPT-4o', { exact: true })).toBeVisible()

    // Tick both via the toggle-all helper.
    await page.getByRole('button', { name: '전체 전환' }).click()

    await page.getByRole('button', { name: '선택 항목 저장' }).click()

    // After save, dialog closes and table shows the new rows.
    await expect(page.getByRole('heading', { name: '모델 추가' })).toBeHidden()
    await expect(page.getByText('Claude 3.5 Sonnet').first()).toBeVisible()
    await expect(page.getByRole('row', { name: /GPT-4o/ })).toBeVisible()
  })

  test('user can register a custom model id', async ({ page }) => {
    let models: Array<Record<string, unknown>> = []

    await page.route(/\/api\/models(\?.*)?$/, (route) => {
      if (route.request().method() === 'POST') {
        const body = route.request().postDataJSON() as Record<string, unknown>
        const created = makeModel(
          String(body.provider),
          String(body.model_name),
          String(body.display_name || body.model_name),
        )
        models = [...models, created]
        return route.fulfill({ status: 201, json: created })
      }
      return route.fulfill({ json: models })
    })

    await page.goto('/models')

    await page
      .getByRole('button', { name: /새 모델|모델 추가/ })
      .first()
      .click()

    await page.getByRole('tab', { name: '사용자 지정 ID' }).click()

    await page.getByLabel('모델 ID').fill('gpt-x-preview')
    await page.getByRole('button', { name: '모델 저장' }).click()

    await expect(page.getByRole('heading', { name: '모델 추가' })).toBeHidden()
    await expect(page.getByText('gpt-x-preview').first()).toBeVisible()
  })
})
