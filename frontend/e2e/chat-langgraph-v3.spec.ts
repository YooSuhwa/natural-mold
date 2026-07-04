import fs from 'node:fs/promises'
import path from 'node:path'
import type { Page } from '@playwright/test'
import { API_BASE, apiDeleteOk, apiPostJson, expect, isRecord, test } from './fixtures'
import {
  FINAL_TEXT,
  NOTES_FILE,
  REPORT_FILE,
  approveExecuteInSkill,
  commandMethod,
  expectFinalTextVisible,
  records,
  sendMessage,
  setupLangGraphV3Agent,
  stringField,
  waitForActiveRun,
  waitForArtifact,
  waitForRunStatus,
} from './langgraph-v3-helpers'
import { waitForThreadStateText } from './langgraph-v3-state-helpers'

const FRONTEND =
  process.env.E2E_BASE_URL ?? `http://localhost:${process.env.E2E_FRONTEND_PORT ?? '3000'}`
const CAPTURE_DIR = path.join('..', 'output', 'e2e-captures', '20260613-langgraph-v3-streaming')
const DESKTOP_VIEWPORT = { width: 1280, height: 720 } as const
const MOBILE_VIEWPORT = { width: 390, height: 844 } as const

async function expectNoHorizontalOverflow(page: Page): Promise<void> {
  await expect
    .poll(
      async () =>
        page.evaluate(
          () => document.documentElement.scrollWidth <= document.documentElement.clientWidth + 1,
        ),
      { timeout: 5_000, intervals: [250, 500] },
    )
    .toBe(true)
}

test.describe('LangGraph v3 chat runtime', () => {
  test.skip(process.env.PW_SKIP_BACKEND === '1', 'Requires the FastAPI backend')
  test.skip(
    process.env.NEXT_PUBLIC_CHAT_RUNTIME === 'legacy',
    'Skipped for the legacy chat runtime',
  )

  test('streams DeepAgents state, subagents, artifacts, usage, replay, and share chips', async ({
    page,
    request,
    browser,
    errors,
  }) => {
    test.setTimeout(180_000)
    await fs.mkdir(CAPTURE_DIR, { recursive: true })
    const setup = await setupLangGraphV3Agent(request)
    const runStartCommands: string[] = []
    page.on('request', (req) => {
      if (commandMethod(req) === 'run.start') runStartCommands.push(req.url())
    })

    try {
      await page.goto(`/agents/${setup.parentAgentId}/conversations/${setup.conversationId}`)
      await sendMessage(page, `E2E_LANGGRAPH_V3 subagent=${setup.childRuntimeName}`)
      const originalRunId = await waitForActiveRun(request, setup.conversationId)
      await waitForRunStatus(request, setup.conversationId, originalRunId, 'interrupted')
      await expect(page.getByText('Collect LangGraph v3 runtime evidence')).toBeVisible({
        timeout: 30_000,
      })
      await expect(page.getByText(/승인이 필요합니다|Approval Required/).last()).toBeVisible()
      await page.screenshot({ path: path.join(CAPTURE_DIR, '01-live-state.png'), fullPage: true })

      await waitForThreadStateText(
        request,
        setup.conversationId,
        'Render delegated subagent progress',
      )
      await page.reload()
      await expect(page.getByText(/승인이 필요합니다|Approval Required/).last()).toBeVisible({
        timeout: 30_000,
      })
      // pending 인터럽트 상태의 리로드 — raw execute_in_skill pill은 승인 카드가
      // 대표하므로 숨겨지고(stripInterruptedRawToolCalls), 카드 헤드라인이 승인
      // 대상 스킬명을 보여준다(resolveApprovalToolName). 승인 완료 후 리로드의
      // SkillExecutionToolUI pill 계약은 captures-wave2-scenario가 검증한다.
      await expect(page.getByText('docx-document').last()).toBeVisible({ timeout: 30_000 })
      expect(runStartCommands).toHaveLength(1)

      await approveExecuteInSkill(page)
      await waitForArtifact(request, setup.conversationId, REPORT_FILE)
      await waitForArtifact(request, setup.conversationId, NOTES_FILE)
      await expectFinalTextVisible(page)
      await expect(page.getByText(setup.childRuntimeName).first()).toBeVisible()
      await expect(page.getByText('E2E subagent scoped result ready.').first()).toBeVisible()

      await page.setViewportSize(MOBILE_VIEWPORT)
      await expectNoHorizontalOverflow(page)
      await page.screenshot({
        path: path.join(CAPTURE_DIR, '03-mobile-thread-state.png'),
        fullPage: true,
      })
      await page.setViewportSize(DESKTOP_VIEWPORT)

      await page.getByRole('button', { name: /파일 패널|Artifacts/ }).click()
      const artifactRail = page.getByRole('complementary')
      const reportArtifactButton = artifactRail
        .getByRole('button', { name: new RegExp(REPORT_FILE) })
        .last()
      const notesArtifactButton = artifactRail
        .getByRole('button', { name: new RegExp(NOTES_FILE) })
        .last()
      await expect(reportArtifactButton).toBeVisible()
      await expect(notesArtifactButton).toBeVisible()
      await reportArtifactButton.click()
      await expect(
        page.getByRole('complementary').getByText('LangGraph v3 E2E Report'),
      ).toBeVisible({ timeout: 20_000 })
      await page.screenshot({
        path: path.join(CAPTURE_DIR, '02-artifact-rail.png'),
        fullPage: true,
      })
      await page.setViewportSize(MOBILE_VIEWPORT)
      await expectNoHorizontalOverflow(page)
      await page.screenshot({
        path: path.join(CAPTURE_DIR, '04-mobile-artifact-rail.png'),
        fullPage: true,
      })
      await page.setViewportSize(DESKTOP_VIEWPORT)

      const tokenButton = page.getByRole('button', { name: /토큰 사용량 보기|Toggle Aria/ }).last()
      await expect(tokenButton).toBeVisible({ timeout: 20_000 })
      await expect(tokenButton).toContainText('165')
      await tokenButton.hover()
      const tokenTooltip = page.getByRole('tooltip').filter({ hasText: /토큰 사용량|Token Usage/ })
      await expect(tokenTooltip).toBeVisible()
      await expect(tokenTooltip).toContainText('120')
      await expect(tokenTooltip).toContainText('45')

      await page.reload()
      await expect(page.getByText(FINAL_TEXT).first()).toBeVisible({ timeout: 30_000 })
      const reloadedArtifactRail = page.getByRole('complementary')
      const reloadedReportButton = reloadedArtifactRail
        .getByRole('button', { name: new RegExp(REPORT_FILE) })
        .last()
      const reloadedReportHeading = reloadedArtifactRail.getByRole('heading', {
        name: REPORT_FILE,
      })
      const reportArtifactIsOpenOrListed = async (): Promise<boolean> =>
        (await reloadedReportButton.isVisible()) || (await reloadedReportHeading.isVisible())
      if (!(await reportArtifactIsOpenOrListed())) {
        await page.getByRole('button', { name: /파일 패널|Artifacts/ }).click()
      }
      await expect
        .poll(reportArtifactIsOpenOrListed, { timeout: 20_000, intervals: [500, 1000] })
        .toBe(true)
      expect(runStartCommands).toHaveLength(1)

      const history = await apiPostJson(
        request,
        `${API_BASE}/api/conversations/${setup.conversationId}/langgraph/threads/${setup.conversationId}/history`,
        setup.csrfHeaders,
        { limit: 1 },
      )
      expect(records(history, 'thread history').length).toBe(1)

      const share = await apiPostJson(
        request,
        `${API_BASE}/api/conversations/${setup.conversationId}/share`,
        setup.csrfHeaders,
      )
      if (!isRecord(share)) throw new Error('share create did not return an object')
      const anonymous = await browser.newContext({ storageState: { cookies: [], origins: [] } })
      try {
        const publicPage = await anonymous.newPage()
        await publicPage.goto(`${FRONTEND}/shared/${stringField(share, 'share_token', 'share')}`)
        await expect(publicPage.getByText(FINAL_TEXT).first()).toBeVisible({ timeout: 20_000 })
        await expect(publicPage.getByText(setup.childRuntimeName).first()).toBeVisible()
      } finally {
        await anonymous.close()
      }

      expect(errors.console).toEqual([])
      expect(errors.network).toEqual([])
    } finally {
      await apiDeleteOk(request, `${API_BASE}/api/agents/${setup.parentAgentId}`, setup.csrfHeaders)
      await apiDeleteOk(request, `${API_BASE}/api/agents/${setup.childAgentId}`, setup.csrfHeaders)
    }
  })

  test('stops an active run through the official SDK cancel endpoint', async ({
    page,
    request,
    errors,
  }) => {
    test.setTimeout(90_000)
    const setup = await setupLangGraphV3Agent(request)

    try {
      await page.goto(`/agents/${setup.parentAgentId}/conversations/${setup.conversationId}`)
      await sendMessage(page, 'E2E_SLOW_STREAM')

      const runId = await waitForActiveRun(request, setup.conversationId)
      const cancelResponsePromise = page.waitForResponse(
        (response) =>
          response.request().method() === 'POST' &&
          response.url().includes(`/threads/${setup.conversationId}/runs/${runId}/cancel`),
      )

      await page.locator('[data-moldy-stop-button="true"]').click()
      const cancelResponse = await cancelResponsePromise
      expect(cancelResponse.ok()).toBeTruthy()

      await waitForRunStatus(request, setup.conversationId, runId, 'canceled')
      await expect(page.locator(`[data-moldy-run-spinner="${setup.conversationId}"]`)).toBeHidden({
        timeout: 10_000,
      })

      expect(errors.console).toEqual([])
      expect(errors.network).toEqual([])
    } finally {
      await apiDeleteOk(request, `${API_BASE}/api/agents/${setup.parentAgentId}`, setup.csrfHeaders)
      await apiDeleteOk(request, `${API_BASE}/api/agents/${setup.childAgentId}`, setup.csrfHeaders)
    }
  })
})
