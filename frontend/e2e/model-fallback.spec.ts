import { test, expect } from './fixtures'

// E2E: Model Fallback UI (M10).
//
// Backend interactions are mocked via Playwright `page.route` so this spec
// runs whether or not FastAPI is up. Coverage:
//
//   1. Open /agents/{id}/settings → Model dialog → Fallback Models section.
//   2. Click "+ Add Fallback" → row appears with default model selected.
//   3. Save → PATCH /api/agents/{id} body includes `model_fallback_ids`.
//   4. "Fallback set" success toast surfaces via the saved-toast text.

const ISO = new Date().toISOString()

const FAKE_MODELS = [
  {
    id: 'model-primary',
    provider: 'openai',
    model_name: 'gpt-4o-mini',
    display_name: 'GPT-4o mini',
    base_url: null,
    is_default: true,
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
    agent_count: 1,
    created_at: ISO,
  },
  {
    id: 'model-fallback-a',
    provider: 'anthropic',
    model_name: 'claude-3-5-haiku',
    display_name: 'Claude 3.5 Haiku',
    base_url: null,
    is_default: false,
    cost_per_input_token: 0.0000008,
    cost_per_output_token: 0.000004,
    context_window: 200000,
    max_output_tokens: 8192,
    input_modalities: ['text'],
    output_modalities: ['text'],
    supports_vision: false,
    supports_function_calling: true,
    supports_reasoning: false,
    source: 'litellm',
    agent_count: 0,
    created_at: ISO,
  },
  {
    id: 'model-fallback-b',
    provider: 'openai',
    model_name: 'gpt-4o',
    display_name: 'GPT-4o',
    base_url: null,
    is_default: false,
    cost_per_input_token: 0.0000025,
    cost_per_output_token: 0.00001,
    context_window: 128000,
    max_output_tokens: 4096,
    input_modalities: ['text'],
    output_modalities: ['text'],
    supports_vision: false,
    supports_function_calling: true,
    supports_reasoning: false,
    source: 'litellm',
    agent_count: 0,
    created_at: ISO,
  },
]

const AGENT_ID = 'agent-fb-1'

type FakeAgent = {
  id: string
  name: string
  description: string | null
  system_prompt: string
  model: { id: string; display_name: string }
  tools: unknown[]
  skills: unknown[]
  sub_agents: unknown[]
  status: string
  is_favorite: boolean
  model_params: { temperature: number; top_p: number; max_tokens: number }
  middleware_configs: unknown[]
  template_id: string | null
  created_at: string
  updated_at: string
  image_url: string | null
  opener_questions: string[]
  llm_credential_id: string | null
  model_fallback_ids: string[] | null
}

const FAKE_AGENT: FakeAgent = {
  id: AGENT_ID,
  name: 'Research Assistant',
  description: null,
  system_prompt: 'You are a helpful researcher.',
  model: { id: 'model-primary', display_name: 'GPT-4o mini' },
  tools: [],
  skills: [],
  sub_agents: [],
  status: 'active',
  is_favorite: false,
  model_params: { temperature: 0.7, top_p: 1.0, max_tokens: 4096 },
  middleware_configs: [],
  template_id: null,
  created_at: ISO,
  updated_at: ISO,
  image_url: null,
  opener_questions: [],
  llm_credential_id: null,
  model_fallback_ids: null,
}

test.describe('Model Fallback', () => {
  test('user can add a fallback model and save', async ({ page }) => {
    let lastPatchBody: Record<string, unknown> | null = null
    let agentSnapshot: typeof FAKE_AGENT = { ...FAKE_AGENT }

    await page.route(/\/api\/models(?:\?.*)?$/, (route) =>
      route.fulfill({ json: FAKE_MODELS }),
    )
    await page.route('**/api/tools', (route) => route.fulfill({ json: [] }))
    await page.route('**/api/skills**', (route) => route.fulfill({ json: [] }))
    await page.route('**/api/middlewares', (route) => route.fulfill({ json: [] }))
    await page.route('**/api/credentials', (route) => route.fulfill({ json: [] }))
    await page.route('**/api/credential-types', (route) =>
      route.fulfill({ json: [] }),
    )
    await page.route(`**/api/agents/${AGENT_ID}/triggers`, (route) =>
      route.fulfill({ json: [] }),
    )

    await page.route(`**/api/agents/${AGENT_ID}`, (route) => {
      const method = route.request().method()
      if (method === 'PUT' || method === 'PATCH') {
        lastPatchBody = route.request().postDataJSON() as Record<string, unknown>
        agentSnapshot = {
          ...agentSnapshot,
          model_fallback_ids: (lastPatchBody.model_fallback_ids ?? null) as
            | string[]
            | null,
        }
        return route.fulfill({ json: agentSnapshot })
      }
      return route.fulfill({ json: agentSnapshot })
    })

    await page.goto(`/agents/${AGENT_ID}/settings`)

    // Wait for the name input to receive the loaded value so we know the page
    // mounted with our mocked agent.
    const nameInput = page.locator('input[placeholder]').first()
    await expect(nameInput).toHaveValue('Research Assistant')

    // Open the Model dialog (configure button next to the model row).
    await page.getByRole('button', { name: /model configuration|모델 설정/i }).click()

    // Fallback section is part of the dialog body.
    const fallbackSection = page.getByTestId('fallback-section')
    await expect(fallbackSection).toBeVisible()

    // Section is closed by default when there are no fallbacks — open it.
    const summary = fallbackSection.locator('summary')
    await summary.click()

    const addButton = page.getByTestId('fallback-add-button')
    await expect(addButton).toBeEnabled()
    await addButton.click()

    // A row appears.
    await expect(page.getByTestId('fallback-row-0')).toBeVisible()
    await expect(page.getByTestId('fallback-select-0')).toBeVisible()

    // Close the dialog so the Save button at the page header is reachable.
    await page.getByRole('button', { name: /^done$|^완료$/i }).click()

    // Save the agent.
    const saveButton = page.getByRole('button', { name: /^save$|^저장$/i })
    await expect(saveButton).toBeEnabled()
    await saveButton.click()

    // Saved toast confirms the PATCH cycle.
    await expect(page.getByText(/저장되었습니다|saved/i).first()).toBeVisible()

    expect(lastPatchBody, 'PATCH body should have been captured').not.toBeNull()
    const patched = lastPatchBody as Record<string, unknown> | null
    expect(patched?.['model_fallback_ids'], 'fallback_ids field present').toBeTruthy()
    const ids = (patched?.['model_fallback_ids'] as string[]) ?? []
    expect(ids.length).toBeGreaterThan(0)
    // Whichever default the dialog picked must be a known fallback model id.
    expect(['model-fallback-a', 'model-fallback-b']).toContain(ids[0])
  })
})
