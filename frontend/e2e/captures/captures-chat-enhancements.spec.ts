import type { APIRequestContext, Page } from '@playwright/test'
import { API_BASE, apiPostJson, isRecord, test, type CsrfHeaders } from '../fixtures'
import {
  sendMessage,
  setupLangGraphV3Agent,
  waitForActiveRun,
  waitForRunStatus,
} from '../langgraph-v3-helpers'
import { capture, DESKTOP_VIEWPORT } from './_capture-helpers'

/**
 * Wave 6 — chat enhancements (user feedback): ask_user shape variants
 * (multi-select, free text, multi-step question_flow), an EXPANDED web-search
 * group, the write_todos plan, the recolored generative-UI chart, and the agent
 * summary popover in the chat header. Gated by E2E_CAPTURE_TOUR=1.
 */

const WAVE = 'wave6-chat-enhancements'

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
  for (let attempt = 1; attempt <= 2; attempt += 1) {
    try {
      await page.goto(`/agents/${agentId}/conversations/${conversationId}`, {
        waitUntil: 'domcontentloaded',
        timeout: 180_000,
      })
      return
    } catch (error) {
      if (attempt === 2) throw error
      await page.waitForTimeout(2_000)
    }
  }
}

async function settleStream(page: Page, timeout = 90_000): Promise<void> {
  const stop = page.locator('[data-moldy-stop-button="true"]:visible').last()
  await stop.waitFor({ state: 'visible', timeout: 10_000 }).catch(() => {})
  await stop.waitFor({ state: 'hidden', timeout }).catch(() => {})
  await page.waitForTimeout(800)
}

test.describe('Wave 6 — chat enhancement captures', () => {
  test.skip(process.env.E2E_CAPTURE_TOUR !== '1', 'Set E2E_CAPTURE_TOUR=1 to run the capture tour')

  test('captures ask_user variants, expanded search, todos, chart, agent popover', async ({
    page,
    request,
  }) => {
    test.setTimeout(600_000)
    await page.setViewportSize(DESKTOP_VIEWPORT)
    const setup = await setupLangGraphV3Agent(request)
    const { parentAgentId: agentId, childAgentId, childRuntimeName, csrfHeaders } = setup

    type Step = { readonly file: string; readonly title: string; readonly run: (cid: string) => Promise<void> }

    const send = async (cid: string, prompt: string): Promise<void> => {
      await gotoChat(page, agentId, cid)
      await sendMessage(page, prompt)
    }

    const steps: Step[] = [
      {
        file: '01-ask-user-single.png',
        title: 'ask_user single',
        run: async (cid) => {
          await send(cid, 'E2E_ASK_USER_FRUIT')
          await page.getByText(/어떤 과일이 좋아요|🍎 사과/).last().waitFor({ state: 'visible', timeout: 30_000 }).catch(() => {})
          await page.waitForTimeout(500)
        },
      },
      {
        file: '02-ask-user-multi.png',
        title: 'ask_user multi-select',
        run: async (cid) => {
          await send(cid, 'E2E_ASK_USER_MULTI')
          await page.getByText(/복수 선택|러닝|클라이밍/).last().waitFor({ state: 'visible', timeout: 30_000 }).catch(() => {})
          await page.waitForTimeout(500)
        },
      },
      {
        file: '03-ask-user-text.png',
        title: 'ask_user free text',
        run: async (cid) => {
          await send(cid, 'E2E_ASK_USER_TEXT')
          await page.getByText(/톤을 자유롭게|어떤 톤으로/).last().waitFor({ state: 'visible', timeout: 30_000 }).catch(() => {})
          await page.waitForTimeout(500)
        },
      },
      {
        file: '04-ask-user-question-flow.png',
        title: 'ask_user question flow',
        run: async (cid) => {
          await send(cid, 'E2E_ASK_USER_FLOW')
          await page.getByText(/여행 선호 조사|어디로 떠나/).last().waitFor({ state: 'visible', timeout: 30_000 }).catch(() => {})
          await page.waitForTimeout(500)
        },
      },
      {
        file: '05-search-expanded.png',
        title: 'web search expanded',
        run: async (cid) => {
          await send(cid, 'E2E_SEARCH_GROUP')
          await settleStream(page)
          // Expand the collapsed search group to reveal the source list.
          const groupHeader = page.getByText(/웹 검색|검색|tavily/).first()
          await groupHeader.click().catch(() => {})
          await page.waitForTimeout(800)
        },
      },
      {
        file: '06-genui-chart-colored.png',
        title: 'recolored generative-UI chart',
        run: async (cid) => {
          await send(cid, 'E2E_UI_DATA_CHART')
          await page.getByTestId('data-ui-chart').last().waitFor({ state: 'visible', timeout: 30_000 }).catch(() => {})
          await settleStream(page)
        },
      },
      {
        file: '07-todos-plan.png',
        title: 'write_todos plan',
        run: async (cid) => {
          await send(cid, `E2E_LANGGRAPH_V3 slow_subagent=true subagent=${childRuntimeName}`)
          const runId = await waitForActiveRun(request, cid).catch(() => '')
          await page.getByText(/Collect LangGraph v3 runtime evidence/).waitFor({ state: 'visible', timeout: 30_000 }).catch(() => {})
          // Wait until the run settles (interrupted) so the capture isn't taken
          // mid-stream — capturing during the slow subagent stream corrupted the PNG.
          if (runId) await waitForRunStatus(request, cid, runId, 'interrupted').catch(() => {})
          await page.waitForTimeout(1_500)
        },
      },
      {
        file: '08-agent-summary-popover.png',
        title: 'agent summary popover',
        run: async (cid) => {
          await gotoChat(page, agentId, cid)
          await page.waitForTimeout(1_500)
          // The chat header shows the agent name + an info affordance; hover/click
          // it to surface the agent summary popover.
          const info = page.locator('header button, [data-slot="chat-header"] button').first()
          await info.hover().catch(() => {})
          await info.click().catch(() => {})
          await page.waitForTimeout(800)
        },
      },
    ]

    try {
      for (const step of steps) {
        try {
          const cid = await freshConversation(request, csrfHeaders, agentId, step.title)
          await step.run(cid)
          await capture(page, WAVE, step.file)
        } catch (error) {
          console.warn(`[capture-tour] enhancement ${step.file} failed: ${String(error)}`)
        }
      }
    } finally {
      await request.delete(`${API_BASE}/api/agents/${agentId}`, { headers: csrfHeaders }).catch(() => {})
      await request.delete(`${API_BASE}/api/agents/${childAgentId}`, { headers: csrfHeaders }).catch(() => {})
    }
  })
})
