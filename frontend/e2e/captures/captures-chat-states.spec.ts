import type { APIRequestContext, Page } from '@playwright/test'
import { API_BASE, apiPostJson, expect, isRecord, test, type CsrfHeaders } from '../fixtures'
import {
  approveExecuteInSkill,
  sendMessage,
  setupLangGraphV3Agent,
  waitForActiveRun,
  waitForRunStatus,
} from '../langgraph-v3-helpers'
import {
  capture,
  captureLocator,
  DESKTOP_VIEWPORT,
  settle,
  warmUpChatRoute,
} from './_capture-helpers'

/**
 * Wave 4 — chat UI state/component matrix. Drives each chat component via the
 * deterministic scripted-model markers and screenshots it. One agent (with the
 * docx skill + a subagent, via setupLangGraphV3Agent) drives everything; each
 * component runs in a FRESH conversation for isolation. Best-effort per item.
 * Gated by E2E_CAPTURE_TOUR=1.
 */

const WAVE = 'wave4-chat-states'

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

async function settleStream(page: Page, timeout = 90_000): Promise<void> {
  const stop = page.locator('[data-moldy-stop-button="true"]:visible').last()
  await stop.waitFor({ state: 'visible', timeout: 10_000 }).catch(() => {})
  await stop.waitFor({ state: 'hidden', timeout }).catch(() => {})
  await page.waitForTimeout(800)
}

test.describe('Wave 4 — chat state captures', () => {
  test.skip(process.env.E2E_CAPTURE_TOUR !== '1', 'Set E2E_CAPTURE_TOUR=1 to run the capture tour')

  // This spec runs first (alphabetical), so it pays the chat route's one-time cold
  // compile. Warm it here, out of the matrix test's 600s budget, so all 14 steps
  // fit. Raise the hook timeout first — the config default (60s) < a cold compile.
  test.beforeAll(async ({ browser }) => {
    test.setTimeout(300_000)
    await warmUpChatRoute(browser)
  })

  test('captures the chat UI component matrix', async ({ page, request }) => {
    test.setTimeout(600_000)
    await page.setViewportSize(DESKTOP_VIEWPORT)
    const setup = await setupLangGraphV3Agent(request)
    const { parentAgentId: agentId, childAgentId, childRuntimeName, csrfHeaders } = setup

    // Each entry: a fresh conversation, send a marker prompt, wait for a ready
    // signal, then capture. Wrapped so one failing component never drops the rest.
    type Step = {
      readonly file: string
      readonly title: string
      readonly run: (conversationId: string) => Promise<void>
      // Capture only the last assistant message element instead of the full page.
      // The chat thread is a nested overflow-y-auto viewport that auto-scrolls to
      // the bottom, so a full-page screenshot clips the top of a tall message.
      readonly captureMessageEl?: boolean
    }

    const gotoChat = async (conversationId: string): Promise<void> => {
      // Retry: the chat route cold-compiles past the first goto budget on a fresh stack.
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

    const goAndSend = async (conversationId: string, prompt: string): Promise<void> => {
      await gotoChat(conversationId)
      await sendMessage(page, prompt)
    }

    const steps: Step[] = [
      {
        file: '01-empty-state.png',
        title: 'Empty chat',
        run: async (conversationId) => {
          await gotoChat(conversationId)
          // Gate on the composer (the route-ready signal sendMessage relies on) so
          // we never screenshot a half-hydrated/blank chat. As the FIRST step this
          // also absorbs the chat route's one-time cold compile, warming it for the
          // rest of the matrix; the timeout is generous for that first compile.
          await page
            .locator('textarea[data-moldy-composer-input="true"]')
            .last()
            .waitFor({ state: 'visible', timeout: 90_000 })
          await settle(page)
        },
      },
      {
        file: '02-rich-markdown.png',
        title: 'Rich markdown',
        captureMessageEl: true,
        run: async (conversationId) => {
          await goAndSend(
            conversationId,
            '체크리스트, 표, TypeScript 코드, 수식, 이미지, 링크, 인용문, Mermaid 다이어그램을 모두 포함해서 채팅 출력 예시를 보여줘',
          )
          await settleStream(page)
          // Mermaid renders to SVG asynchronously after the text settles.
          await page.locator('svg').first().waitFor({ state: 'visible', timeout: 15_000 }).catch(() => {})
          await page.waitForTimeout(1_500)
        },
      },
      {
        file: '03-tool-group.png',
        title: 'Tool grouping',
        run: async (conversationId) => {
          await goAndSend(conversationId, 'E2E_TOOL_GROUP')
          await settleStream(page)
        },
      },
      {
        file: '04-search-group.png',
        title: 'Search aggregate',
        run: async (conversationId) => {
          await goAndSend(conversationId, 'E2E_SEARCH_GROUP')
          await settleStream(page)
        },
      },
      {
        file: '05-ask-user.png',
        title: 'ask_user option list',
        run: async (conversationId) => {
          await goAndSend(conversationId, 'E2E_ASK_USER_FRUIT')
          await page
            .getByText(/어떤 과일이 좋아요|🍎 사과|입력이 필요합니다/)
            .last()
            .waitFor({ state: 'visible', timeout: 30_000 })
            .catch(() => {})
          await settleStream(page)
        },
      },
      {
        file: '06-genui-table.png',
        title: 'Generative UI table',
        run: async (conversationId) => {
          await goAndSend(conversationId, 'E2E_UI_DATA_TABLE')
          await page.getByTestId('data-ui-data-table').last().waitFor({ state: 'visible', timeout: 30_000 }).catch(() => {})
          await settleStream(page)
        },
      },
      {
        file: '07-genui-chart.png',
        title: 'Generative UI chart',
        run: async (conversationId) => {
          await goAndSend(conversationId, 'E2E_UI_DATA_CHART')
          await page.getByTestId('data-ui-chart').last().waitFor({ state: 'visible', timeout: 30_000 }).catch(() => {})
          await settleStream(page)
        },
      },
      {
        file: '08-genui-stats.png',
        title: 'Generative UI stats',
        run: async (conversationId) => {
          await goAndSend(conversationId, 'E2E_UI_DATA_STATS')
          await page.getByTestId('data-ui-stats').last().waitFor({ state: 'visible', timeout: 30_000 }).catch(() => {})
          await settleStream(page)
        },
      },
      {
        file: '09-genui-terminal.png',
        title: 'Generative UI terminal',
        run: async (conversationId) => {
          await goAndSend(conversationId, 'E2E_UI_DATA_TERMINAL')
          await page.getByTestId('data-ui-terminal').last().waitFor({ state: 'visible', timeout: 30_000 }).catch(() => {})
          await settleStream(page)
        },
      },
      {
        file: '10-hitl-approval.png',
        title: 'HITL approval card',
        run: async (conversationId) => {
          await goAndSend(conversationId, '도구를 사용해서 문서를 만들어줘. 승인 후 실행해.')
          await expect(page.getByText(/승인이 필요합니다|Approval Required/).last()).toBeVisible({
            timeout: 40_000,
          })
          await page.waitForTimeout(600)
        },
      },
      {
        file: '11-hitl-multi.png',
        title: 'HITL multi-action',
        run: async (conversationId) => {
          await goAndSend(conversationId, 'E2E_HITL_MULTI')
          await expect(page.getByText(/승인이 필요합니다|Approval Required/).last()).toBeVisible({
            timeout: 40_000,
          })
          await page.waitForTimeout(600)
        },
      },
      {
        file: '12-artifact-inline.png',
        title: 'Generated artifact (inline)',
        run: async (conversationId) => {
          await goAndSend(conversationId, 'E2E_DOCX 문서를 생성해줘')
          await approveExecuteInSkill(page).catch(() => {})
          await settleStream(page, 120_000)
        },
      },
      {
        file: '13-langgraph-v3-planning.png',
        title: 'Planning + subagent + deepagents panel',
        run: async (conversationId) => {
          await goAndSend(
            conversationId,
            `E2E_LANGGRAPH_V3 slow_subagent=true subagent=${childRuntimeName}`,
          )
          const runId = await waitForActiveRun(request, conversationId)
          await page
            .getByText('Collect LangGraph v3 runtime evidence')
            .waitFor({ state: 'visible', timeout: 30_000 })
            .catch(() => {})
          await waitForRunStatus(request, conversationId, runId, 'interrupted').catch(() => {})
          await page.waitForTimeout(800)
        },
      },
      {
        file: '14-branch-picker.png',
        title: 'Regenerate branch picker',
        run: async (conversationId) => {
          await goAndSend(conversationId, '오늘 날씨 어때?')
          await settleStream(page)
          await page.getByRole('button', { name: '재생성' }).first().click().catch(() => {})
          await expect(page.getByText('2/2').first()).toBeVisible({ timeout: 40_000 }).catch(() => {})
          await page.waitForTimeout(600)
        },
      },
    ]

    try {
      for (const step of steps) {
        try {
          const conversationId = await freshConversation(request, csrfHeaders, agentId, step.title)
          await step.run(conversationId)
          if (step.captureMessageEl) {
            const el = page.locator('[data-moldy-message-role="assistant"]').last()
            await el.scrollIntoViewIfNeeded().catch(() => {})
            await captureLocator(el, WAVE, step.file)
          } else {
            await capture(page, WAVE, step.file)
          }
        } catch (error) {
          console.warn(`[capture-tour] chat-state ${step.file} failed: ${String(error)}`)
        }
      }
    } finally {
      await request.delete(`${API_BASE}/api/agents/${agentId}`, { headers: csrfHeaders }).catch(() => {})
      await request.delete(`${API_BASE}/api/agents/${childAgentId}`, { headers: csrfHeaders }).catch(() => {})
    }
  })
})
