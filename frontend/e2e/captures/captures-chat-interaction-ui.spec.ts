import type { APIRequestContext, Page } from '@playwright/test'

import {
  API_BASE,
  apiDeleteOk,
  apiPostJson,
  expect,
  isRecord,
  test,
  type CsrfHeaders,
} from '../fixtures'
import { sendForStrategy } from '../chat-message-queue-actions'
import {
  sendMessage,
  setupLangGraphV3Agent,
  waitForActiveRun,
  waitForRunStatus,
} from '../langgraph-v3-helpers'
import { captureLocator, captureViewport, settle, warmUpChatRoute } from './_capture-helpers'

const WAVE = 'wave-chat-interaction-ui-after'
const DESKTOP = { width: 1366, height: 900 } as const
const MOBILE = { width: 390, height: 844 } as const

async function freshConversation(
  request: APIRequestContext,
  csrfHeaders: CsrfHeaders,
  agentId: string,
  title: string,
): Promise<string> {
  const value = await apiPostJson(
    request,
    `${API_BASE}/api/agents/${agentId}/conversations`,
    csrfHeaders,
    { title },
  )
  if (!isRecord(value) || typeof value.id !== 'string') {
    throw new Error('conversation create failed')
  }
  return value.id
}

async function gotoChat(page: Page, agentId: string, conversationId: string): Promise<void> {
  await page.goto(`/agents/${agentId}/conversations/${conversationId}`, {
    waitUntil: 'domcontentloaded',
    timeout: 180_000,
  })
}

async function captureAskUser(
  page: Page,
  request: APIRequestContext,
  csrfHeaders: CsrfHeaders,
  agentId: string,
  marker: string,
  expectedText: RegExp,
  filename: string,
): Promise<void> {
  const conversationId = await freshConversation(request, csrfHeaders, agentId, filename)
  await gotoChat(page, agentId, conversationId)
  await sendMessage(page, marker)
  await expect(page.getByText(expectedText).last()).toBeVisible({ timeout: 30_000 })
  const toolUi = page.locator('[data-tool-ui-id]').last()
  const card =
    (await toolUi.count()) > 0
      ? toolUi.locator('xpath=../..')
      : page
          .getByText(/입력이 필요합니다|Input Required/)
          .last()
          .locator('xpath=ancestor::div[contains(@class,"moldy-chat-card")][1]')
  await expect(card).toBeVisible()
  await captureLocator(card, WAVE, filename)
}

async function runCapture(label: string, captureStep: () => Promise<void>): Promise<void> {
  try {
    await captureStep()
  } catch (error) {
    console.warn(`[capture-tour] chat interaction ${label} failed: ${String(error)}`)
  }
}

test.describe('Chat interaction UI comparison captures', () => {
  test.skip(process.env.E2E_CAPTURE_TOUR !== '1', 'Set E2E_CAPTURE_TOUR=1 to run the capture tour')

  test.beforeAll(async ({ browser }) => {
    test.setTimeout(300_000)
    await warmUpChatRoute(browser)
  })

  test('captures ask_user, HITL, queue/steer, and planning after states', async ({
    page,
    request,
  }) => {
    test.setTimeout(600_000)
    await page.setViewportSize(DESKTOP)
    const setup = await setupLangGraphV3Agent(request)
    const { parentAgentId: agentId, childAgentId, childRuntimeName, csrfHeaders } = setup

    try {
      await runCapture('ask_user single', async () => {
        await captureAskUser(
          page,
          request,
          csrfHeaders,
          agentId,
          'E2E_ASK_USER_FRUIT',
          /어떤 과일이 좋아요|🍎 사과/,
          '01-ask-user-single.png',
        )
        await page.getByRole('option', { name: /직접 입력|Custom answer/ }).last().click()
        await page.setViewportSize(MOBILE)
        await captureViewport(page, WAVE, '11-ask-user-custom-mobile.png')
        await page.setViewportSize(DESKTOP)
      })
      await runCapture('ask_user multi', () =>
        captureAskUser(
          page,
          request,
          csrfHeaders,
          agentId,
          'E2E_ASK_USER_MULTI',
          /복수 선택|러닝|클라이밍/,
          '02-ask-user-multi.png',
        ),
      )
      await runCapture('ask_user text', () =>
        captureAskUser(
          page,
          request,
          csrfHeaders,
          agentId,
          'E2E_ASK_USER_TEXT',
          /톤을 자유롭게|어떤 톤으로/,
          '03-ask-user-text.png',
        ),
      )
      await runCapture('ask_user flow', () =>
        captureAskUser(
          page,
          request,
          csrfHeaders,
          agentId,
          'E2E_ASK_USER_FLOW',
          /여행 선호 조사|어디로 떠나/,
          '04-ask-user-question-flow.png',
        ),
      )

      await runCapture('HITL single', async () => {
        const conversationId = await freshConversation(
          request,
          csrfHeaders,
          agentId,
          'HITL single after',
        )
        await gotoChat(page, agentId, conversationId)
        await sendMessage(page, '문서 생성 도구를 사용해 승인 후 실행해줘')
        const approval = page.locator('[data-testid^="approval-action-"]').last()
        await expect(approval).toBeVisible({ timeout: 40_000 })
        await captureLocator(approval, WAVE, '07-hitl-single.png')
      })

      await runCapture('HITL multiple', async () => {
        const conversationId = await freshConversation(
          request,
          csrfHeaders,
          agentId,
          'HITL multi after',
        )
        await gotoChat(page, agentId, conversationId)
        await sendMessage(page, 'E2E_HITL_MULTI')
        const group = page.getByTestId('approval-group')
        await expect(group).toBeVisible({ timeout: 40_000 })
        await expect(page.locator('[data-testid^="approval-action-"]')).toHaveCount(2)
        await expect(
          page.locator('[data-testid^="approval-action-"][data-hitl-active="true"]'),
        ).toHaveCount(1)
        await captureLocator(group, WAVE, '08-hitl-multiple.png')
        await group
          .locator('[data-hitl-active="true"]')
          .getByTestId('approval-approve-button')
          .click()
        await expect(group).toHaveAttribute('data-hitl-active-action', '1')
        await captureLocator(group, WAVE, '13-hitl-multiple-action-2.png')
        await page.setViewportSize(MOBILE)
        await captureViewport(page, WAVE, '14-hitl-multiple-mobile.png')
        await page.setViewportSize(DESKTOP)
      })

      await runCapture('queue and steer', async () => {
        const conversationId = await freshConversation(request, csrfHeaders, agentId, 'Queue after')
        const active = await request.post(
          `${API_BASE}/api/e2e/conversations/${conversationId}/runs`,
          {
            headers: csrfHeaders,
            data: { status: 'running', source: 'chat', input_preview: 'queue capture fixture' },
          },
        )
        expect(active.ok()).toBeTruthy()
        await gotoChat(page, agentId, conversationId)
        const firstQueueItem = await sendForStrategy(page, '첫 번째 대기 메시지', 'enqueue')
        await sendForStrategy(page, '두 번째 대기 메시지', 'enqueue')
        const queuePanel = page.locator('[data-moldy-message-queue]')
        await expect(queuePanel).toBeVisible()
        await captureLocator(queuePanel, WAVE, '05-message-queue.png')
        const firstItem = page.locator(`[data-moldy-queue-item="${firstQueueItem.inputId}"]`)
        await firstItem.locator('button[aria-expanded]').click()
        await expect(firstItem.locator('[role="group"]')).toBeVisible()
        await captureLocator(queuePanel, WAVE, '12-message-queue-actions.png')
        await firstItem.locator('button[aria-expanded]').click()
        await page.locator(`[data-moldy-queue-steer="${firstQueueItem.inputId}"]`).click()
        await expect(
          page.locator(`[data-moldy-queue-send-now="${firstQueueItem.inputId}"]`),
        ).toBeVisible()
        await captureLocator(queuePanel, WAVE, '06-steer-active-run.png')
        await page.setViewportSize(MOBILE)
        await captureViewport(page, WAVE, '15-steer-active-run-mobile.png')
        await page.setViewportSize(DESKTOP)
      })

      await runCapture('planning', async () => {
        const conversationId = await freshConversation(
          request,
          csrfHeaders,
          agentId,
          'Planning after',
        )
        await gotoChat(page, agentId, conversationId)
        await sendMessage(page, `E2E_LANGGRAPH_V3 slow_subagent=true subagent=${childRuntimeName}`)
        const runId = await waitForActiveRun(request, conversationId)
        await expect(page.locator('[data-moldy-mission-control="true"]')).toBeVisible({
          timeout: 30_000,
        })
        await waitForRunStatus(request, conversationId, runId, 'interrupted')
        await settle(page, 600)
        await page.setViewportSize(DESKTOP)
        await captureViewport(page, WAVE, '09-planning-desktop.png')
        await page.setViewportSize(MOBILE)
        await captureViewport(page, WAVE, '10-planning-mobile.png')
      })
    } finally {
      await apiDeleteOk(request, `${API_BASE}/api/agents/${agentId}`, csrfHeaders)
      await apiDeleteOk(request, `${API_BASE}/api/agents/${childAgentId}`, csrfHeaders)
    }
  })
})
