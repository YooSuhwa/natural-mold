import type { APIRequestContext, Page } from '@playwright/test'
import { API_BASE, apiGetJson, isRecord, loginApi, test, type CsrfHeaders } from '../fixtures'
import { sendMessage, setupLangGraphV3Agent, waitForActiveRun } from '../langgraph-v3-helpers'
import {
  capture,
  createConversation,
  DESKTOP_VIEWPORT,
  scriptedModelId,
} from './_capture-helpers'

/**
 * Wave 9 (feedback): the background-run lifecycle — a chat run keeps going after
 * you leave the conversation; the sidebar session row shows a spinner while it
 * runs, an attention icon when it needs input, and an unread badge once it
 * finishes. Gated by E2E_CAPTURE_TOUR=1.
 */

const WAVE = 'wave9-run-status'

async function gotoCommit(page: Page, url: string): Promise<void> {
  await page.goto(url, { waitUntil: 'commit', timeout: 120_000 }).catch(() => {})
}

async function runStatus(
  request: APIRequestContext,
  conversationId: string,
  runId: string,
): Promise<string | null> {
  const run = await apiGetJson(
    request,
    `${API_BASE}/api/conversations/${conversationId}/runs/${runId}`,
  ).catch(() => null)
  return isRecord(run) && typeof run.status === 'string' ? run.status : null
}

test.describe('Wave 9 — background run status captures', () => {
  test.skip(process.env.E2E_CAPTURE_TOUR !== '1', 'Set E2E_CAPTURE_TOUR=1 to run the capture tour')

  test.beforeEach(async ({ page }) => {
    await page.setViewportSize(DESKTOP_VIEWPORT)
  })

  test('spinner while running, then unread badge when complete (left the session)', async ({
    page,
    request,
  }) => {
    test.setTimeout(180_000)
    const csrf: CsrfHeaders = await loginApi(request)
    const modelId = await scriptedModelId(request)
    const created = await request.post(`${API_BASE}/api/agents`, {
      headers: csrf,
      data: { name: '백그라운드 실행 데모', system_prompt: '천천히 응답.', model_id: modelId },
    })
    const agent = (await created.json()) as { id: string }
    try {
      const running = await createConversation(request, csrf, agent.id, '길게 도는 작업')
      const other = await createConversation(request, csrf, agent.id, '다른 대화')

      await gotoCommit(page, `/agents/${agent.id}/conversations/${running}`)
      // A long (~22s) streaming run that keeps going after we navigate away.
      await sendMessage(page, 'E2E_VISUAL_SLOW_STREAM')
      const runId = await waitForActiveRun(request, running)

      // Leave the running conversation for another one of the same agent — the
      // sidebar keeps the agent expanded so the running session row is visible.
      await gotoCommit(page, `/agents/${agent.id}/conversations/${other}`)
      const spinner = page.locator(`[data-moldy-run-spinner="${running}"]`)
      await spinner.waitFor({ state: 'visible', timeout: 25_000 }).catch(() => {})
      await page.waitForTimeout(400)
      await capture(page, WAVE, '01-session-running-spinner.png')

      // Wait for the run to finish in the background.
      await expect_poll_complete(request, running, runId)
      await spinner.waitFor({ state: 'hidden', timeout: 30_000 }).catch(() => {})
      await page.waitForTimeout(1_200)
      await capture(page, WAVE, '02-session-completed-badge.png')
    } finally {
      await request.delete(`${API_BASE}/api/agents/${agent.id}`, { headers: csrf }).catch(() => {})
    }

    async function expect_poll_complete(
      req: APIRequestContext,
      cid: string,
      runId: string,
    ): Promise<void> {
      for (let i = 0; i < 40; i += 1) {
        const status = await runStatus(req, cid, runId)
        if (status && !['queued', 'running', 'streaming'].includes(status)) return
        await page.waitForTimeout(1_500)
      }
    }
  })

  test('attention icon when a background run needs input (HITL)', async ({ page, request }) => {
    test.setTimeout(180_000)
    const setup = await setupLangGraphV3Agent(request)
    try {
      const other = await createConversation(request, setup.csrfHeaders, setup.parentAgentId, '다른 대화')
      await gotoCommit(page, `/agents/${setup.parentAgentId}/conversations/${setup.conversationId}`)
      await sendMessage(
        page,
        `E2E_LANGGRAPH_V3 slow_subagent=true subagent=${setup.childRuntimeName}`,
      )
      await waitForActiveRun(request, setup.conversationId)
      // Leave the conversation; the run continues and eventually interrupts (HITL).
      await gotoCommit(page, `/agents/${setup.parentAgentId}/conversations/${other}`)
      const attention = page.locator(`[data-moldy-run-attention="${setup.conversationId}"]`)
      const spinner = page.locator(`[data-moldy-run-spinner="${setup.conversationId}"]`)
      await Promise.race([
        attention.waitFor({ state: 'visible', timeout: 60_000 }).catch(() => {}),
        spinner.waitFor({ state: 'visible', timeout: 60_000 }).catch(() => {}),
      ])
      await page.waitForTimeout(800)
      await capture(page, WAVE, '03-session-attention-or-running.png')
    } finally {
      await request.delete(`${API_BASE}/api/agents/${setup.parentAgentId}`, { headers: setup.csrfHeaders }).catch(() => {})
      await request.delete(`${API_BASE}/api/agents/${setup.childAgentId}`, { headers: setup.csrfHeaders }).catch(() => {})
    }
  })
})
