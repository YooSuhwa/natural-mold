import fs from 'node:fs/promises'
import path from 'node:path'
import type { APIRequestContext, Page } from '@playwright/test'
import {
  API_BASE,
  apiDeleteOk,
  apiPostJson,
  expect,
  failWithBody,
  isRecord,
  test,
} from './fixtures'
import {
  FINAL_TEXT,
  NOTES_FILE,
  REPORT_FILE,
  approveExecuteInSkill,
  expectFinalTextVisible,
  normalizeArtifactList,
  sendMessage,
  setupLangGraphV3Agent,
  stringField,
  waitForActiveRun,
  waitForArtifact,
  waitForRunStatus,
  type LangGraphV3Setup,
} from './langgraph-v3-helpers'

const FRONTEND =
  process.env.E2E_BASE_URL ?? `http://localhost:${process.env.E2E_FRONTEND_PORT ?? '3000'}`
const CAPTURE_DIR = path.join('..', 'output', 'captures')
const DESKTOP_VIEWPORT = { width: 1366, height: 900 } as const
const MOBILE_VIEWPORT = { width: 390, height: 844 } as const
const ASSISTANT_THREAD_VIEWPORTS = [
  { name: 'mobile-375', width: 375, height: 844 },
  { name: 'tablet-768', width: 768, height: 900 },
  { name: 'desktop-1280', width: 1280, height: 900 },
] as const
const TERMINAL_RUN_STATUS_PATTERN = /^(completed|failed|interrupted|canceled|stale|gone)$/

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

async function capture(page: Page, filename: string, fullPage = true): Promise<void> {
  await page.screenshot({ path: path.join(CAPTURE_DIR, filename), fullPage })
}

async function waitForCapturePaint(page: Page): Promise<void> {
  await page.evaluate(
    () =>
      new Promise<void>((resolve) => {
        requestAnimationFrame(() => {
          requestAnimationFrame(() => resolve())
        })
      }),
  )
}

async function captureAssistantThreadViewportMatrix(page: Page): Promise<void> {
  // This matrix is behavior-neutral parity evidence; the pre-existing 375px document-width
  // blocker belongs to Todo 22, while the older 390px overflow assertion below remains unchanged.
  for (const viewport of ASSISTANT_THREAD_VIEWPORTS) {
    await page.setViewportSize({ width: viewport.width, height: viewport.height })
    const inlineRail = page.locator('[data-slot="chat-right-rail"]')
    const overlayRail = page.locator('[role="dialog"]')
    if (viewport.width === 768) {
      await expect(overlayRail).toBeVisible()
      await expect(inlineRail).toBeHidden()
    }
    if (viewport.width === 1280) {
      await expect(overlayRail).toBeHidden()
      await expect(inlineRail).toBeVisible()
    }
    // At tablet widths the artifact rail is a full-screen dialog. The underlying
    // chat button can still satisfy `isVisible()` while the dialog intercepts
    // pointer events, so only scroll the chat when the rail is inline.
    if (viewport.width >= 1280) {
      const scrollToBottomButton = page.getByRole('button', {
        name: /맨 아래로 이동|Scroll to bottom/,
      })
      if ((await scrollToBottomButton.isVisible()) && (await scrollToBottomButton.isEnabled())) {
        await scrollToBottomButton.click()
      }
    }
    await waitForCapturePaint(page)
    const filename = `task-14-completed-thread-${viewport.name}.png`
    // The matrix records the exact viewport; fixed overlay rails must not use
    // Playwright's full-page stitching path.
    await capture(page, filename, false)
    const file = await fs.stat(path.join(CAPTURE_DIR, filename))
    expect(file.size).toBeGreaterThan(0)
  }

  expect(ASSISTANT_THREAD_VIEWPORTS.map(({ width }) => width)).toEqual([375, 768, 1280])
}

async function expectApprovalCardVisible(page: Page): Promise<void> {
  const approvalCard = page.getByText(/승인이 필요합니다|Approval Required/).last()
  if (!(await approvalCard.isVisible())) {
    await page.reload()
  }
  await expect(approvalCard).toBeVisible({ timeout: 30_000 })
}

async function deleteSetup(request: APIRequestContext, setup: LangGraphV3Setup): Promise<void> {
  await settleActiveRun(request, setup)
  await apiDeleteOk(request, `${API_BASE}/api/agents/${setup.parentAgentId}`, setup.csrfHeaders)
  await apiDeleteOk(request, `${API_BASE}/api/agents/${setup.childAgentId}`, setup.csrfHeaders)
}

async function settleActiveRun(request: APIRequestContext, setup: LangGraphV3Setup): Promise<void> {
  const activeRun = await activeCancelableRunId(request, setup.conversationId)
  if (!activeRun) return

  const cancelUrl = `${API_BASE}/api/conversations/${setup.conversationId}/runs/${activeRun}/cancel`
  const cancelResponse = await request.post(cancelUrl, { headers: setup.csrfHeaders })
  if (!cancelResponse.ok() && cancelResponse.status() !== 404 && cancelResponse.status() !== 409) {
    await failWithBody(`POST ${cancelUrl}`, cancelResponse)
  }
  if (cancelResponse.status() === 404) return

  await expect
    .poll(async () => runStatus(request, setup.conversationId, activeRun), {
      timeout: 45_000,
      intervals: [500, 1000, 2000],
    })
    .toMatch(TERMINAL_RUN_STATUS_PATTERN)
}

async function activeCancelableRunId(
  request: APIRequestContext,
  conversationId: string,
): Promise<string | null> {
  const activeUrl = `${API_BASE}/api/conversations/${conversationId}/runs/active`
  const response = await request.get(activeUrl)
  if (response.status() === 404) return null
  if (!response.ok()) {
    await failWithBody(`GET ${activeUrl}`, response)
  }
  const body: unknown = await response.json()
  if (!isRecord(body)) return null
  const id = body.id
  if (typeof id !== 'string' || !id) return null

  switch (body.status) {
    case 'queued':
    case 'running':
    case 'canceling':
      return id
    default:
      return null
  }
}

async function runStatus(
  request: APIRequestContext,
  conversationId: string,
  runId: string,
): Promise<string> {
  const response = await request.get(
    `${API_BASE}/api/conversations/${conversationId}/runs/${runId}`,
  )
  if (response.status() === 404) return 'gone'
  if (!response.ok()) return `http-${response.status()}`
  const body: unknown = await response.json()
  return isRecord(body) && typeof body.status === 'string' ? body.status : 'unknown'
}

test.describe('LangGraph v3 visual scenario matrix', () => {
  test.skip(process.env.PW_SKIP_BACKEND === '1', 'Requires the FastAPI backend')
  test.skip(
    process.env.NEXT_PUBLIC_CHAT_RUNTIME === 'legacy',
    'Skipped for the legacy chat runtime',
  )

  test.beforeEach(async ({ page }) => {
    await fs.mkdir(CAPTURE_DIR, { recursive: true })
    await page.setViewportSize(DESKTOP_VIEWPORT)
  })

  test('captures planning, subagent, and HITL pending states', async ({
    page,
    request,
    errors,
  }) => {
    test.setTimeout(180_000)
    const setup = await setupLangGraphV3Agent(request)

    try {
      await page.goto(`/agents/${setup.parentAgentId}/conversations/${setup.conversationId}`)
      await sendMessage(
        page,
        `E2E_LANGGRAPH_V3 slow_subagent=true subagent=${setup.childRuntimeName}`,
      )
      const runId = await waitForActiveRun(request, setup.conversationId)

      // `.first()`: while the run is still streaming the planning todos render in
      // two places at once — the live activity panel ("작업 목록") and the message
      // "Plan" card — so a bare `getByText` trips Playwright strict mode. Either
      // copy being visible proves the planning state was captured. (The duplicate
      // render itself is addressed separately by gating the activity panel.)
      await expect(page.getByText('Collect LangGraph v3 runtime evidence').first()).toBeVisible({
        timeout: 30_000,
      })
      const streamingStatusPanel = page.locator('[data-slot="streaming-status-panel"]')
      const planPill = page
        .locator('.moldy-tool-pill')
        .filter({ hasText: 'Collect LangGraph v3 runtime evidence' })
        .first()
      await expect(streamingStatusPanel).toBeVisible()
      await expect(planPill).toBeVisible()
      await expect
        .poll(async () => {
          const [statusBox, planBox] = await Promise.all([
            streamingStatusPanel.boundingBox(),
            planPill.boundingBox(),
          ])
          if (!statusBox || !planBox) return false
          return statusBox.y + statusBox.height <= planBox.y
        })
        .toBe(true)
      await waitForCapturePaint(page)
      await capture(page, '01-running-subagent-and-planning.png')

      await expect(page.getByText(/E2E subagent visual matrix:/).first()).toBeVisible({
        timeout: 30_000,
      })
      await waitForRunStatus(request, setup.conversationId, runId, 'interrupted')
      await expectApprovalCardVisible(page)
      await capture(page, '02-hitl-tool-approval.png')

      expect(errors.console).toEqual([])
      expect(errors.network).toEqual([])
    } finally {
      await deleteSetup(request, setup)
    }
  })

  test('captures completed HITL, artifacts, share, and mobile states', async ({
    page,
    request,
    browser,
    errors,
  }) => {
    // Cold isolated stacks may spend up to 20 seconds synchronizing the artifact
    // index before this scenario captures five viewport/share states.
    test.setTimeout(240_000)
    const setup = await setupLangGraphV3Agent(request)

    try {
      await page.goto(`/agents/${setup.parentAgentId}/conversations/${setup.conversationId}`)
      await sendMessage(page, `E2E_LANGGRAPH_V3 subagent=${setup.childRuntimeName}`)
      const runId = await waitForActiveRun(request, setup.conversationId)
      await waitForRunStatus(request, setup.conversationId, runId, 'interrupted')
      await expectApprovalCardVisible(page)
      await approveExecuteInSkill(page)
      await waitForArtifact(request, setup.conversationId, REPORT_FILE)
      await waitForArtifact(request, setup.conversationId, NOTES_FILE)
      await expectFinalTextVisible(page)
      // G10: the live/replayed thread received the `moldy.subagent_names` map, so
      // the subagent pill now shows the human-readable child agent name.
      await expect(page.getByText(setup.childName).first()).toBeVisible()
      await capture(page, '03-completed-thread-with-subagent.png')

      await page.setViewportSize(DESKTOP_VIEWPORT)
      const parityArtifactRail = page.getByRole('complementary')
      const { reportArtifactButton: parityReportArtifactButton } = await normalizeArtifactList(
        page,
        REPORT_FILE,
        NOTES_FILE,
      )
      await expect(parityArtifactRail).toBeVisible()
      await parityReportArtifactButton.dispatchEvent('click')
      await expect(parityArtifactRail.getByText('LangGraph v3 E2E Report')).toBeVisible({
        timeout: 20_000,
      })

      await captureAssistantThreadViewportMatrix(page)

      await parityArtifactRail.getByRole('button', { name: 'Close panel' }).click()
      await expect(parityArtifactRail).toBeHidden()

      await page.setViewportSize(MOBILE_VIEWPORT)
      await expectNoHorizontalOverflow(page)
      await capture(page, '04-mobile-thread.png')
      await page.setViewportSize(DESKTOP_VIEWPORT)

      const tokenButton = page.getByRole('button', { name: /토큰 사용량 보기|Toggle Aria/ }).last()
      await expect(tokenButton).toBeVisible({ timeout: 20_000 })
      await tokenButton.hover()
      const tokenUsageTooltip = page
        .getByRole('tooltip')
        .filter({ hasText: /토큰 사용량|Token Usage/ })
      await expect(tokenUsageTooltip).toBeVisible()
      await expect(tokenUsageTooltip).toHaveCSS('opacity', '1')
      await waitForCapturePaint(page)
      await capture(page, '05-token-usage-tooltip.png')
      await page.mouse.move(1, 1)

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
      await capture(page, '06-artifact-rail-desktop.png')

      await page.setViewportSize(MOBILE_VIEWPORT)
      await expectNoHorizontalOverflow(page)
      await capture(page, '07-mobile-artifact-rail.png')
      await page.setViewportSize(DESKTOP_VIEWPORT)

      const share = await apiPostJson(
        request,
        `${API_BASE}/api/conversations/${setup.conversationId}/share`,
        setup.csrfHeaders,
      )
      if (!isRecord(share)) throw new Error('share create did not return an object')
      const anonymous = await browser.newContext({
        storageState: { cookies: [], origins: [] },
        viewport: DESKTOP_VIEWPORT,
      })
      try {
        const publicPage = await anonymous.newPage()
        await publicPage.goto(`${FRONTEND}/shared/${stringField(share, 'share_token', 'share')}`)
        await expect(publicPage.getByText(FINAL_TEXT).first()).toBeVisible({ timeout: 20_000 })
        // The public share view renders a static snapshot with no live stream, so
        // the `moldy.subagent_names` side-channel never arrives (anonymous context,
        // empty store) — the pill keeps the raw runtime name here. Tag display
        // names on the share snapshot is out of G10-A scope.
        await expect(publicPage.getByText(setup.childRuntimeName).first()).toBeVisible()
        const metadataBadges = publicPage.locator(
          '[data-slot="share-hero-summary"] [data-slot="badge"]',
        )
        await expect(metadataBadges).toHaveCount(2)
        const metadataBoxes = await metadataBadges.evaluateAll((badges) =>
          badges.map((badge) => {
            const box = badge.getBoundingClientRect()
            return { left: box.left, right: box.right, top: box.top, bottom: box.bottom }
          }),
        )
        for (let leftIndex = 0; leftIndex < metadataBoxes.length; leftIndex += 1) {
          for (let rightIndex = leftIndex + 1; rightIndex < metadataBoxes.length; rightIndex += 1) {
            const left = metadataBoxes[leftIndex]
            const right = metadataBoxes[rightIndex]
            const rowsOverlap = left.top < right.bottom && right.top < left.bottom
            if (rowsOverlap) expect(left.right <= right.left || right.right <= left.left).toBe(true)
          }
        }
        const summaryBox = await publicPage
          .locator('[data-slot="share-hero-summary"]')
          .boundingBox()
        const descriptionBox = await publicPage
          .locator('[data-slot="share-hero-description"]')
          .boundingBox()
        if (!summaryBox || !descriptionBox) {
          throw new Error('Share summary and description must both have layout boxes')
        }
        expect(summaryBox.y + summaryBox.height <= descriptionBox.y).toBe(true)
        await capture(publicPage, '08-share-page-subagent-chip.png')
      } finally {
        await anonymous.close()
      }

      expect(errors.console).toEqual([])
      expect(errors.network).toEqual([])
    } finally {
      await deleteSetup(request, setup)
    }
  })

  test('captures active streaming and completed stream states', async ({
    page,
    request,
    errors,
  }) => {
    test.setTimeout(90_000)
    const setup = await setupLangGraphV3Agent(request)

    try {
      await page.goto(`/agents/${setup.parentAgentId}/conversations/${setup.conversationId}`)
      await sendMessage(page, 'E2E_VISUAL_SLOW_STREAM')
      await expect(page.getByText('E2E_VISUAL_SLOW_STREAM')).toBeVisible({ timeout: 20_000 })
      await waitForCapturePaint(page)
      await capture(page, '09-active-streaming-response.png')

      await expect(page.getByText(/fixture complete\./).first()).toBeVisible({
        timeout: 60_000,
      })
      await capture(page, '10-completed-stream-response.png')

      expect(errors.console).toEqual([])
      expect(errors.network).toEqual([])
    } finally {
      await deleteSetup(request, setup)
    }
  })
})
