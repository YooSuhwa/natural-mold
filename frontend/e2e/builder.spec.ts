import { test, expect } from './fixtures'

// Real conversational-builder journey against the live backend + the seeded
// System LLM (LiteLLM). The builder is a multi-turn meta-agent whose free-text
// wording varies run to run, so we assert on the deterministic, template-driven
// pipeline scaffolding (session label + phase/progress tracker) rather than
// exact LLM prose.
test.describe('Conversational builder', () => {
  test.skip(
    process.env.PW_SKIP_BACKEND === '1',
    'Requires the FastAPI backend and a real System LLM',
  )

  test('starts a session and runs the build pipeline from an initial message', async ({ page }) => {
    test.setTimeout(90_000)
    const prompt = 'Create an agent named GreetBot that greets users warmly.'
    await page.goto(`/agents/new/conversational?initialMessage=${encodeURIComponent(prompt)}`)

    // POST /api/builder created a session (label shows in the header).
    await expect(page.getByText(/세션 #/)).toBeVisible({ timeout: 30_000 })

    // The user's request is echoed into the thread.
    await expect(page.getByText(prompt).first()).toBeVisible()

    // The builder (LiteLLM) responds and the multi-phase pipeline begins —
    // the progress tracker is template-driven, so it's a stable signal that the
    // real LLM produced a structured response (not just that a session exists).
    await expect(page.getByText('진행 상황').first()).toBeVisible({ timeout: 60_000 })
    await expect(page.getByText('프로젝트 초기화').first()).toBeVisible()
  })
})
