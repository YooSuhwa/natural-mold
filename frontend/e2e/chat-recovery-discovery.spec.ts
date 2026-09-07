import type { APIRequestContext, Page, Response, TestInfo } from '@playwright/test'
import { API_BASE, apiDeleteOk, apiGetJson, expect, isRecord, test } from './fixtures'
import {
  NOTES_FILE,
  REPORT_FILE,
  approveExecuteInSkill,
  commandMethod,
  expectFinalTextVisible,
  normalizeArtifactList,
  sendMessageForRun,
  setupLangGraphV3Agent,
  waitForAcceptedRunStart,
  waitForArtifact,
  waitForRunStatus,
  type LangGraphV3Setup,
} from './langgraph-v3-helpers'

const MOBILE_VIEWPORT = { width: 390, height: 844 } as const
const TABLET_VIEWPORT = { width: 768, height: 900 } as const
const DESKTOP_VIEWPORT = { width: 1440, height: 900 } as const

async function capture(page: Page, testInfo: TestInfo, name: string): Promise<void> {
  const file = testInfo.outputPath(name)
  await page.screenshot({ path: file, fullPage: false, animations: 'disabled' })
  await testInfo.attach(name, { path: file, contentType: 'image/png' })
}

function isExactCancelResponse(response: Response, conversationId: string, runId: string): boolean {
  if (response.request().method() !== 'POST') return false

  const responseUrl = new URL(response.url())
  const apiUrl = new URL(API_BASE)
  return (
    responseUrl.origin === apiUrl.origin &&
    responseUrl.pathname ===
      `/api/conversations/${encodeURIComponent(conversationId)}/runs/${encodeURIComponent(runId)}/cancel` &&
    responseUrl.search === '' &&
    responseUrl.hash === ''
  )
}

async function deleteSetup(request: APIRequestContext, setup: LangGraphV3Setup): Promise<void> {
  await apiDeleteOk(request, `${API_BASE}/api/agents/${setup.parentAgentId}`, setup.csrfHeaders)
  await apiDeleteOk(request, `${API_BASE}/api/agents/${setup.childAgentId}`, setup.csrfHeaders)
}

test.describe('Task 8 recovery and discovery browser acceptance', () => {
  test.skip(process.env.PW_SKIP_BACKEND === '1', 'Requires the scripted backend lane')

  test('opens the real empty command catalog without sending, then retries the exact failed run once', async ({
    page,
    request,
    errors,
  }, testInfo) => {
    test.setTimeout(180_000)
    const setup = await setupLangGraphV3Agent(request)
    const runStartCommands: string[] = []
    page.on('request', (request_) => {
      if (
        commandMethod(request_) === 'run.start' &&
        request_.url().includes(setup.conversationId)
      ) {
        runStartCommands.push(request_.postData() ?? '')
      }
    })

    try {
      await page.setViewportSize(MOBILE_VIEWPORT)
      await page.goto(`/agents/${setup.parentAgentId}/conversations/${setup.conversationId}`, {
        waitUntil: 'domcontentloaded',
      })
      const composer = page.locator('textarea[data-moldy-composer-input="true"]').last()
      await expect(composer).toBeVisible()

      await page.getByRole('button', { name: /명령어 찾아보기|Browse commands/ }).click()
      await expect(composer).toHaveValue('/')
      await expect(composer).toBeFocused()
      await expect(page.getByRole('listbox')).toBeVisible()
      expect(runStartCommands).toHaveLength(0)
      await capture(page, testInfo, '390-command-discovery-no-send.png')

      await page.setViewportSize(TABLET_VIEWPORT)
      const failedRunId = await sendMessageForRun(
        page,
        setup.conversationId,
        'E2E_ERROR task8 retry',
      )
      await waitForRunStatus(request, setup.conversationId, failedRunId, 'failed')
      const retryButton = page.getByRole('button', { name: '다시 시도' })
      await expect(retryButton).toBeVisible({ timeout: 30_000 })
      await capture(page, testInfo, '768-failed-run-before-retry.png')

      const priorRunStartCount = runStartCommands.length
      const retryRunId = await waitForAcceptedRunStart(page, setup.conversationId, () =>
        retryButton.dblclick(),
      )
      expect(runStartCommands).toHaveLength(priorRunStartCount + 1)
      expect(runStartCommands.at(-1)).toContain('E2E_ERROR task8 retry')
      expect(retryRunId).not.toBe(failedRunId)
      await waitForRunStatus(request, setup.conversationId, retryRunId, 'failed')
      await expect(page.getByRole('button', { name: '다시 시도' })).toBeVisible({ timeout: 30_000 })

      expect(errors.console).toEqual([])
      expect(errors.network).toEqual([])
    } finally {
      await deleteSetup(request, setup)
    }
  })

  test('keeps the generated-artifact rail accessible across desktop/mobile and waits for server cancellation acknowledgement', async ({
    page,
    request,
    errors,
  }, testInfo) => {
    test.setTimeout(240_000)
    const setup = await setupLangGraphV3Agent(request)

    try {
      await page.setViewportSize(DESKTOP_VIEWPORT)
      await page.goto(`/agents/${setup.parentAgentId}/conversations/${setup.conversationId}`, {
        waitUntil: 'domcontentloaded',
      })
      const artifactRunId = await sendMessageForRun(
        page,
        setup.conversationId,
        `E2E_LANGGRAPH_V3 subagent=${setup.childRuntimeName}`,
      )
      await waitForRunStatus(request, setup.conversationId, artifactRunId, 'interrupted')
      await approveExecuteInSkill(page)
      await waitForArtifact(request, setup.conversationId, REPORT_FILE)
      await waitForArtifact(request, setup.conversationId, NOTES_FILE)
      await expectFinalTextVisible(page)

      const { reportArtifactButton } = await normalizeArtifactList(page, REPORT_FILE, NOTES_FILE)
      await reportArtifactButton.click()
      const desktopRail = page.getByRole('complementary')
      await expect(desktopRail).toBeVisible()
      await expect(desktopRail.getByText('LangGraph v3 E2E Report')).toBeVisible()
      await capture(page, testInfo, '1440-artifact-rail-inline.png')

      await page.setViewportSize(MOBILE_VIEWPORT)
      const mobileRail = page.getByRole('dialog')
      await expect(mobileRail).toBeVisible()
      // Current ko/en `chat.rightRail.closePanel` both render this accessible label.
      const closePanel = mobileRail.getByRole('button', { name: 'Close panel', exact: true })
      await expect(closePanel).toBeVisible()
      await capture(page, testInfo, '390-artifact-rail-overlay.png')
      await closePanel.click()
      await expect(mobileRail).toBeHidden()

      await page.setViewportSize(TABLET_VIEWPORT)
      const activeRunId = await sendMessageForRun(
        page,
        setup.conversationId,
        'E2E_SLOW_STREAM task8 cancel',
      )
      const cancelResponse = page.waitForResponse((response) =>
        isExactCancelResponse(response, setup.conversationId, activeRunId),
      )
      await page.locator('[data-moldy-stop-button="true"]').click()
      expect((await cancelResponse).ok()).toBe(true)
      await waitForRunStatus(request, setup.conversationId, activeRunId, 'canceled')
      await expect
        .poll(
          async () => {
            const run = await apiGetJson(
              request,
              `${API_BASE}/api/conversations/${setup.conversationId}/runs/${activeRunId}`,
            )
            if (!isRecord(run) || !isRecord(run.metrics)) return null

            const metrics = run.metrics
            const values = [
              metrics.root_tool_calls,
              metrics.descendant_tool_calls,
              metrics.root_subagent_calls,
              metrics.descendant_subagent_calls,
            ]
            return values.every((value) => typeof value === 'number') ? values : null
          },
          { timeout: 15_000, intervals: [250, 500, 1_000] },
        )
        .toEqual([0, 0, 0, 0])
      const canceledSummary = page.locator(
        `[data-testid="run-summary"][data-run-id="${activeRunId}"]`,
      )
      await expect(canceledSummary).toBeVisible({ timeout: 15_000 })
      await expect(canceledSummary).toContainText('도구 총 0')
      await expect(canceledSummary).toContainText('서브 에이전트 총 0')
      const canceledNotice = page.locator(`[data-moldy-message-id="moldy-canceled-${activeRunId}"]`)
      await expect(canceledNotice).toBeVisible({ timeout: 15_000 })
      await expect(canceledNotice).toContainText(/중단됨|Canceled/)
      await expect(page.locator('[data-moldy-stop-button="true"]')).toHaveCount(0)
      await expect(page.getByRole('button', { name: /전송|Send/ }).last()).toBeVisible()
      await capture(page, testInfo, '768-server-canceled.png')

      expect(errors.console).toEqual([])
      expect(errors.network).toEqual([])
    } finally {
      await deleteSetup(request, setup)
    }
  })
})
