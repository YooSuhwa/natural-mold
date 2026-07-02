import { API_BASE, apiPostJson, expect, isRecord, test } from './fixtures'
import { sendMessage, setupLangGraphV3Agent } from './langgraph-v3-helpers'

/**
 * G5 (conversation export) + G6 (in-conversation search) contract. Sends a
 * message, then verifies the navigator menu export triggers a file download and
 * the Ctrl+F search overlay finds a match. Requires the scripted model
 * (E2E_SCRIPTED_MODEL_ENABLED=true).
 */
test.describe('Chat export + in-conversation search (G5/G6)', () => {
  test('export downloads a markdown file and search finds a match', async ({ page, request }) => {
    test.setTimeout(180_000)
    const setup = await setupLangGraphV3Agent(request)
    const { parentAgentId: agentId, csrfHeaders } = setup

    const convo = await apiPostJson(
      request,
      `${API_BASE}/api/agents/${agentId}/conversations`,
      csrfHeaders,
      { title: 'export-search' },
    )
    if (!isRecord(convo) || typeof convo.id !== 'string') throw new Error('conversation create failed')
    const conversationId = convo.id

    await page.goto(`/agents/${agentId}/conversations/${conversationId}`, {
      waitUntil: 'domcontentloaded',
      timeout: 180_000,
    })
    await page
      .locator('textarea[data-moldy-composer-input="true"]')
      .last()
      .waitFor({ state: 'visible', timeout: 90_000 })

    await sendMessage(page, '검색 대상 회의 메시지입니다')
    // Wait for the scripted answer to settle (stop button appears then hides).
    const stop = page.locator('[data-moldy-stop-button="true"]:visible').last()
    await stop.waitFor({ state: 'visible', timeout: 10_000 }).catch(() => {})
    await stop.waitFor({ state: 'hidden', timeout: 90_000 }).catch(() => {})

    // G5 — navigator session menu → 내보내기 → Markdown triggers a download.
    await page.getByRole('button', { name: '대화 메뉴' }).first().click()
    await page.getByRole('menuitem', { name: '내보내기' }).click()
    const downloadPromise = page.waitForEvent('download')
    await page.getByRole('button', { name: 'Markdown (.md)' }).click()
    const download = await downloadPromise
    expect(download.suggestedFilename()).toMatch(/^conversation-.*\.md$/)

    // G6 — Ctrl/Cmd+F overlay finds the sent message ("회의").
    await page.locator('textarea[data-moldy-composer-input="true"]').last().click()
    await page.keyboard.press('ControlOrMeta+f')
    const overlay = page.getByRole('search')
    await expect(overlay).toBeVisible({ timeout: 10_000 })
    await overlay.getByRole('textbox').fill('회의')
    // A match shows an "N/M" counter.
    await expect(overlay.getByText(/\d+\/\d+/)).toBeVisible({ timeout: 10_000 })
  })
})
