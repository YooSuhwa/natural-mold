import type { APIRequestContext, Page } from '@playwright/test'
import { API_BASE, apiPostJson, expect, isRecord, test, type CsrfHeaders } from '../fixtures'
import { sendMessage, setupLangGraphV3Agent } from '../langgraph-v3-helpers'
import { capture, DESKTOP_VIEWPORT, settle, warmUpChatRoute } from './_capture-helpers'

/**
 * Wave — chat export (G5) + in-conversation search (G6). Sends a message, then
 * screenshots the export dialog (navigator session menu → 내보내기) and the
 * Ctrl+F search overlay. Gated by E2E_CAPTURE_TOUR=1.
 */

const WAVE = 'wave-chat-export-search'

async function freshConversation(
  request: APIRequestContext,
  csrfHeaders: CsrfHeaders,
  agentId: string,
  title: string,
): Promise<string> {
  const convo = await apiPostJson(
    request,
    `${API_BASE}/api/agents/${agentId}/conversations`,
    csrfHeaders,
    { title },
  )
  if (!isRecord(convo) || typeof convo.id !== 'string') throw new Error('conversation create failed')
  return convo.id
}

async function gotoChat(page: Page, agentId: string, conversationId: string): Promise<void> {
  await page.goto(`/agents/${agentId}/conversations/${conversationId}`, {
    waitUntil: 'domcontentloaded',
    timeout: 180_000,
  })
  await page
    .locator('textarea[data-moldy-composer-input="true"]')
    .last()
    .waitFor({ state: 'visible', timeout: 90_000 })
}

async function settleStream(page: Page): Promise<void> {
  const stop = page.locator('[data-moldy-stop-button="true"]:visible').last()
  await stop.waitFor({ state: 'visible', timeout: 10_000 }).catch(() => {})
  await stop.waitFor({ state: 'hidden', timeout: 90_000 }).catch(() => {})
  await page.waitForTimeout(800)
}

test.describe('Chat export + search captures', () => {
  test.skip(process.env.E2E_CAPTURE_TOUR !== '1', 'Set E2E_CAPTURE_TOUR=1 to run the capture tour')

  test.beforeAll(async ({ browser }) => {
    test.setTimeout(300_000)
    await warmUpChatRoute(browser)
  })

  test('captures export dialog and search overlay', async ({ page, request }) => {
    test.setTimeout(300_000)
    await page.setViewportSize(DESKTOP_VIEWPORT)
    const setup = await setupLangGraphV3Agent(request)
    const { parentAgentId: agentId, csrfHeaders } = setup

    const conversationId = await freshConversation(request, csrfHeaders, agentId, '내보내기·검색 캡쳐')
    await gotoChat(page, agentId, conversationId)
    await sendMessage(page, '오늘 회의 내용을 요약해줘')
    await settleStream(page)

    // G5 — navigator session menu → 내보내기 dialog.
    await page.getByRole('button', { name: '대화 메뉴' }).first().click()
    await page.getByRole('menuitem', { name: '내보내기' }).click()
    await expect(page.getByRole('button', { name: 'Markdown (.md)' })).toBeVisible({
      timeout: 15_000,
    })
    await settle(page)
    await capture(page, WAVE, '01-export-dialog.png')
    await page.keyboard.press('Escape')
    await page.waitForTimeout(400)

    // G6 — Ctrl/Cmd+F search overlay + a match.
    await page.locator('textarea[data-moldy-composer-input="true"]').last().click()
    await page.keyboard.press('ControlOrMeta+f')
    const overlay = page.getByRole('search')
    await expect(overlay).toBeVisible({ timeout: 10_000 })
    await overlay.getByRole('textbox').fill('회의')
    await page.waitForTimeout(600)
    await settle(page)
    await capture(page, WAVE, '02-search-overlay.png')
  })
})
