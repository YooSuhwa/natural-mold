import { test, expect } from './fixtures'

// E2E: M11 — Model rankings column on /models.
//
// Mocks the FastAPI backend so the spec runs with PW_SKIP_BACKEND=1. Coverage:
// 1. The DataTable surfaces LMArena / LiveBench / AA columns, missing
//    rankings render as a muted em-dash, and the LMArena header sort pushes
//    populated rows above unranked ones.
// 2. ModelEditDialog (row click) shows the Rankings section with each score
//    rendered in its formatted shape (integer for LMArena, 1-decimal for
//    LiveBench / AA Index).

const NOW = new Date().toISOString()

const FAKE_CRED_TYPES: unknown[] = []
const FAKE_CREDENTIALS: unknown[] = []

function makeModel(overrides: Partial<Record<string, unknown>>) {
  return {
    id: 'model-base',
    provider: 'openai',
    model_name: 'gpt-base',
    display_name: 'Base Model',
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
    source: 'litellm',
    agent_count: 0,
    rankings: null,
    created_at: NOW,
    ...overrides,
  }
}

const MODELS = [
  makeModel({
    id: 'm-claude',
    provider: 'anthropic',
    model_name: 'claude-3-5-sonnet',
    display_name: 'Claude 3.5 Sonnet',
    rankings: { lmarena: 1287, livebench: 78.4, aa_index: 65.2 },
  }),
  makeModel({
    id: 'm-gpt4o',
    provider: 'openai',
    model_name: 'gpt-4o',
    display_name: 'GPT-4o',
    rankings: { lmarena: 1265, livebench: 75.1, aa_index: 60.9 },
  }),
  makeModel({
    id: 'm-mystery',
    provider: 'openai',
    model_name: 'mystery-preview',
    display_name: 'Mystery Preview',
    source: 'manual',
    rankings: null,
  }),
]

test.describe('M11 — Model ranking column', () => {
  test.beforeEach(async ({ page }) => {
    await page.route('**/api/credential-types', (route) =>
      route.fulfill({ json: FAKE_CRED_TYPES }),
    )
    await page.route('**/api/credentials', (route) =>
      route.fulfill({ json: FAKE_CREDENTIALS }),
    )
    await page.route('**/api/health/**', (route) => route.fulfill({ json: [] }))
    await page.route('**/api/models**', (route) => {
      if (route.request().method() === 'GET') {
        return route.fulfill({ json: MODELS })
      }
      return route.fulfill({ json: MODELS })
    })
  })

  test('ranking columns render with proper formatting and missing-data fallback', async ({
    page,
  }) => {
    await page.goto('/models')

    // Page rendered with the catalog visible.
    await expect(page.getByRole('heading', { name: /^models$/i })).toBeVisible()
    await expect(page.getByText('Claude 3.5 Sonnet')).toBeVisible()
    await expect(page.getByText('GPT-4o', { exact: true })).toBeVisible()
    await expect(page.getByText('Mystery Preview')).toBeVisible()

    // Each ranking column header is present and the LMArena tooltip target
    // is rendered as a focusable element (role="img" with aria-label).
    await expect(
      page.getByRole('button', { name: /lmarena/i }).first(),
    ).toBeVisible()

    // LMArena values format as integers.
    await expect(page.getByText('1287').first()).toBeVisible()
    await expect(page.getByText('1265').first()).toBeVisible()

    // LiveBench / AA values format with one decimal.
    await expect(page.getByText('78.4').first()).toBeVisible()
    await expect(page.getByText('65.2').first()).toBeVisible()

    // Missing rankings render as em-dash in the Mystery Preview row.
    const mysteryRow = page.getByRole('row', { name: /mystery preview/i })
    await expect(mysteryRow).toBeVisible()
    // The row must contain the em-dash placeholder for LMArena/LiveBench/AA.
    await expect(mysteryRow.getByText('—').first()).toBeVisible()
  })

  test('sorting by LMArena pins missing rows below populated rows', async ({
    page,
  }) => {
    await page.goto('/models')

    // First click → asc, second click → desc. Either way Mystery Preview
    // (no ranking) must remain at the bottom.
    const lmarenaHeader = page.getByRole('button', { name: /lmarena/i }).first()
    await lmarenaHeader.click() // asc
    await lmarenaHeader.click() // desc

    const rows = page.getByRole('row')
    // First non-header row should be one of the ranked models. The unranked
    // model must appear below at least one ranked model, no matter the order.
    const allRowText = await rows.allTextContents()
    const ranked = allRowText.findIndex((t) => /Claude 3\.5 Sonnet|GPT-4o/.test(t))
    const unranked = allRowText.findIndex((t) => /Mystery Preview/.test(t))
    expect(ranked).toBeGreaterThan(-1)
    expect(unranked).toBeGreaterThan(ranked)
  })

  test('row click opens edit dialog with rankings section populated', async ({
    page,
  }) => {
    await page.goto('/models')

    await page.getByText('Claude 3.5 Sonnet').click()

    // Dialog appears with the Benchmark rankings section.
    const rankingsCard = page.getByTestId('model-rankings')
    await expect(rankingsCard).toBeVisible()
    await expect(rankingsCard.getByText('1287')).toBeVisible()
    await expect(rankingsCard.getByText('78.4')).toBeVisible()
    await expect(rankingsCard.getByText('65.2')).toBeVisible()
  })

  test('edit dialog shows the Custom-ID empty hint for manual models', async ({
    page,
  }) => {
    await page.goto('/models')

    await page.getByText('Mystery Preview').click()

    const rankingsCard = page.getByTestId('model-rankings')
    await expect(rankingsCard).toBeVisible()
    await expect(
      rankingsCard.getByText(/custom id models are not auto-matched/i),
    ).toBeVisible()
  })

  test('"Has ranking" toggle filters out unranked rows without removing the toggle', async ({
    page,
  }) => {
    await page.goto('/models')

    await page.getByTestId('only-with-ranking').click()

    await expect(page.getByText('Claude 3.5 Sonnet')).toBeVisible()
    await expect(page.getByText('GPT-4o', { exact: true })).toBeVisible()
    await expect(page.getByText('Mystery Preview')).toBeHidden()

    // Toggle remains rendered so the user can switch back.
    await expect(page.getByTestId('only-with-ranking')).toBeVisible()
  })
})
