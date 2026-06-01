import { test, expect } from './fixtures'

// E2E: Health check (M9) — model status column + Check now + Health tab chart.
//
// Backend is mocked via Playwright `page.route` so this spec runs whether or
// not FastAPI is up. Coverage:
//   1. /models renders Status column with mock healthy entry, "Check now"
//      triggers a probe, table refreshes with the new entry.
//   2. Row click → Health tab → HealthHistoryChart renders 30 mock entries.

const NOW = new Date().toISOString()
const ISO = (offsetSec: number) => new Date(Date.now() - offsetSec * 1000).toISOString()

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

const HEALTH_HEALTHY = {
  id: 'h-latest-1',
  target_kind: 'model',
  target_id: 'model-1',
  status: 'healthy',
  latency_ms: 312,
  error_kind: null,
  error_message: null,
  checked_at: ISO(45),
}

// 30-entry chronological history with mixed statuses to exercise the chart.
const HEALTH_HISTORY = Array.from({ length: 30 }, (_, i) => {
  const status = i % 9 === 0 ? 'unhealthy' : i % 5 === 0 ? 'degraded' : 'healthy'
  return {
    id: `h-${i}`,
    target_kind: 'model',
    target_id: 'model-1',
    status,
    latency_ms: status === 'unhealthy' ? null : 200 + (i % 7) * 35,
    error_kind: status === 'unhealthy' ? 'timeout' : null,
    error_message: status === 'unhealthy' ? 'request timed out' : null,
    checked_at: ISO((30 - i) * 600),
  }
})

const HEALTH_AFTER_CHECK = {
  id: 'h-fresh-1',
  target_kind: 'model',
  target_id: 'model-1',
  status: 'degraded',
  latency_ms: 1450,
  error_kind: null,
  error_message: null,
  checked_at: new Date().toISOString(),
}

test.describe('Health check', () => {
  test.beforeEach(async ({ page }) => {
    await page.route('**/api/credential-types', (route) => route.fulfill({ json: FAKE_CRED_TYPES }))
    await page.route('**/api/credentials', (route) => route.fulfill({ json: FAKE_CREDENTIALS }))
    await page.route('**/api/models', (route) => route.fulfill({ json: [FAKE_MODEL] }))
  })

  test('Status column + Check now refreshes the chip', async ({ page }) => {
    let healthCallCount = 0
    await page.route('**/api/health/models', (route) => {
      healthCallCount += 1
      // After the manual check, return the new degraded snapshot.
      route.fulfill({
        json: healthCallCount === 1 ? [HEALTH_HEALTHY] : [HEALTH_AFTER_CHECK],
      })
    })
    await page.route('**/api/health/check**', (route) =>
      route.fulfill({ json: HEALTH_AFTER_CHECK }),
    )
    // History feed is loaded lazily by detail panels — provide an empty array
    // so any incidental request resolves cleanly.
    await page.route('**/api/health/history**', (route) => route.fulfill({ json: [] }))

    await page.goto('/models')

    await expect(page.getByRole('heading', { name: '모델' })).toBeVisible()
    await expect(page.getByText('GPT-4o mini')).toBeVisible()

    // Initial healthy chip
    await expect(page.getByText('정상', { exact: true }).first()).toBeVisible()

    // Click "상태 확인" action — request fires, list refetches, chip swaps to 주의.
    await page.getByTestId('check-now-model-1').click()
    await expect(page.getByText('주의', { exact: true }).first()).toBeVisible()
  })

  test('Row click → Health tab → 30-entry chart', async ({ page }) => {
    await page.route('**/api/health/models', (route) => route.fulfill({ json: [HEALTH_HEALTHY] }))
    await page.route('**/api/health/history**', (route) => route.fulfill({ json: HEALTH_HISTORY }))

    await page.goto('/models')

    // Open the edit dialog by clicking the row
    await page.getByText('GPT-4o mini').click()

    // Switch to the Health tab
    await page.getByTestId('health-tab').click()

    // The chart container should be visible
    await expect(page.getByTestId('health-history-chart')).toBeVisible()
    // 30 timeline cells (status strip)
    const timeline = page.getByTestId('status-timeline')
    await expect(timeline).toBeVisible()
    await expect(timeline.locator('[data-status]')).toHaveCount(30)

    // Latest probe metadata is surfaced in the panel header
    await expect(page.getByText(/최근 프로브/)).toBeVisible()
  })
})
