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
const CAPTURE_DIR = path.join('..', 'output', 'e2e-captures', '20260614-langgraph-v3-visual-matrix')
const DESKTOP_VIEWPORT = { width: 1366, height: 900 } as const
const MOBILE_VIEWPORT = { width: 390, height: 844 } as const
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

async function capture(page: Page, filename: string): Promise<void> {
  await page.screenshot({ path: path.join(CAPTURE_DIR, filename), fullPage: true })
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
    .poll(
      async () => runStatus(request, setup.conversationId, activeRun),
      { timeout: 45_000, intervals: [500, 1000, 2000] },
    )
    .toMatch(TERMINAL_RUN_STATUS_PATTERN)
}

async function activeCancelableRunId(request: APIRequestContext, conversationId: string): Promise<string | null> {
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
  const response = await request.get(`${API_BASE}/api/conversations/${conversationId}/runs/${runId}`)
  if (response.status() === 404) return 'gone'
  if (!response.ok()) return `http-${response.status()}`
  const body: unknown = await response.json()
  return isRecord(body) && typeof body.status === 'string' ? body.status : 'unknown'
}

test.describe('LangGraph v3 visual scenario matrix', () => {
  test.skip(process.env.PW_SKIP_BACKEND === '1', 'Requires the FastAPI backend')
  test.skip(
    process.env.NEXT_PUBLIC_CHAT_RUNTIME !== 'langgraph_v3',
    'Requires NEXT_PUBLIC_CHAT_RUNTIME=langgraph_v3',
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

      await expect(page.getByText('Collect LangGraph v3 runtime evidence')).toBeVisible({
        timeout: 30_000,
      })
      await page.waitForTimeout(700)
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
    test.setTimeout(180_000)
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
      await expect(page.getByText(setup.childRuntimeName).first()).toBeVisible()
      await capture(page, '03-completed-thread-with-subagent.png')

      await page.setViewportSize(MOBILE_VIEWPORT)
      await expectNoHorizontalOverflow(page)
      await capture(page, '04-mobile-thread.png')
      await page.setViewportSize(DESKTOP_VIEWPORT)

      const tokenButton = page.getByRole('button', { name: /토큰 사용량 보기|Toggle Aria/ }).last()
      await expect(tokenButton).toBeVisible({ timeout: 20_000 })
      await tokenButton.hover()
      await expect(
        page.getByRole('tooltip').filter({ hasText: /토큰 사용량|Token Usage/ }),
      ).toBeVisible()
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
        await expect(publicPage.getByText(setup.childRuntimeName).first()).toBeVisible()
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
      await page.waitForTimeout(700)
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
