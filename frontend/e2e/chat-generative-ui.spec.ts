import { API_BASE, apiDeleteOk, expect, test } from './fixtures'
import { sendMessage, setupLangGraphV3Agent } from './langgraph-v3-helpers'

/**
 * Generative UI Phase 1 demo (chat-generative-ui-dev-plan §7.3). The scripted
 * E2E_UI_DATA_DEMO fixture calls the e2e_ui_data_demo tool whose JSON result
 * projects into a moldy.ui_data (demo_note) event; the converter injects a data
 * part that the registry renders as DemoNoteCard. Proves the end-to-end pipeline
 * live + after reload (C10).
 *
 * Uses the scripted keyless model (E2E_SCRIPTED_MODEL_ENABLED) — CI-runnable.
 */
test.describe('Chat generative UI (ui_data demo)', () => {
  test.skip(process.env.PW_SKIP_BACKEND === '1', 'Requires the FastAPI backend')
  test.skip(process.env.NEXT_PUBLIC_CHAT_RUNTIME === 'legacy', 'Skipped for the legacy chat runtime')

  test('renders a demo_note data part in the bubble and keeps it after reload', async ({
    page,
    request,
    errors,
  }) => {
    // Generous budget: the FIRST cold run compiles the chat route on `goto`
    // (Next dev), which alone can take >60s before the streamed run even starts.
    test.setTimeout(180_000)
    const setup = await setupLangGraphV3Agent(request)

    // Backend E2E_UI_DATA_DEMO (e2e_scripted_model.py): the demo tool returns
    // {"ui_type":"demo_note","text": DEMO_TEXT}; the final turn streams FINAL_TEXT.
    const DEMO_TEXT = 'E2E generative UI demo note.'
    const FINAL_TEXT = 'E2E generative UI demo rendered.'
    const demoNote = page.locator('[data-testid="data-ui-demo-note"]')
    const userBubbles = page.locator('[data-moldy-message-role="user"]')

    try {
      await page.goto(`/agents/${setup.parentAgentId}/conversations/${setup.conversationId}`, {
        waitUntil: 'domcontentloaded',
      })
      await sendMessage(page, 'E2E_UI_DATA_DEMO 제너레이티브 UI 데모 렌더 확인')

      // The run streams to completion (the demo tool is READ_ONLY, no HITL).
      await expect(page.getByText(FINAL_TEXT).last()).toBeVisible({ timeout: 60_000 })

      // The demo_note data part renders as the typed component with its props.
      await expect(demoNote).toHaveCount(1, { timeout: 15_000 })
      await expect(demoNote).toContainText(DEMO_TEXT)
      await expect(userBubbles).toHaveCount(1)

      // Reload → the ui_data custom event replays and the data part re-renders
      // from the rehydrated history (same characteristic as artifacts).
      await page.reload({ waitUntil: 'domcontentloaded' })
      await expect(page.getByText(FINAL_TEXT).last()).toBeVisible({ timeout: 60_000 })
      await expect(demoNote).toHaveCount(1, { timeout: 15_000 })
      await expect(demoNote).toContainText(DEMO_TEXT)

      // No page JS exceptions during the run or reload.
      expect(errors.console, 'console errors during generative UI demo').toEqual([])
    } finally {
      await apiDeleteOk(request, `${API_BASE}/api/agents/${setup.parentAgentId}`, setup.csrfHeaders)
      await apiDeleteOk(request, `${API_BASE}/api/agents/${setup.childAgentId}`, setup.csrfHeaders)
    }
  })

  test('renders a data_table data part with headers and rows (Phase 2)', async ({
    page,
    request,
    errors,
  }) => {
    test.setTimeout(180_000)
    const setup = await setupLangGraphV3Agent(request)

    // Backend E2E_UI_DATA_TABLE → e2e_ui_data_demo(kind=data_table) → a
    // {"ui_type":"data_table", columns, rows} payload → DataTableCard.
    const FINAL_TEXT = 'E2E generative UI demo rendered.'
    const table = page.locator('[data-testid="data-ui-data-table"]')

    try {
      await page.goto(`/agents/${setup.parentAgentId}/conversations/${setup.conversationId}`, {
        waitUntil: 'domcontentloaded',
      })
      await sendMessage(page, 'E2E_UI_DATA_TABLE 데이터 테이블 렌더 확인')

      await expect(page.getByText(FINAL_TEXT).last()).toBeVisible({ timeout: 60_000 })
      await expect(table).toHaveCount(1, { timeout: 15_000 })
      // Headers + a cell value from the scripted fixture.
      await expect(table.getByText('이름')).toBeVisible()
      await expect(table.getByText('Alice')).toBeVisible()
      await expect(table.getByText('95')).toBeVisible()

      // Reload → re-renders from the replayed ui_data event.
      await page.reload({ waitUntil: 'domcontentloaded' })
      await expect(page.getByText(FINAL_TEXT).last()).toBeVisible({ timeout: 60_000 })
      await expect(table).toHaveCount(1, { timeout: 15_000 })
      await expect(table.getByText('Alice')).toBeVisible()

      expect(errors.console, 'console errors during data_table render').toEqual([])
    } finally {
      await apiDeleteOk(request, `${API_BASE}/api/agents/${setup.parentAgentId}`, setup.csrfHeaders)
      await apiDeleteOk(request, `${API_BASE}/api/agents/${setup.childAgentId}`, setup.csrfHeaders)
    }
  })
})
