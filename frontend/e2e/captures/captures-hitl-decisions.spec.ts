import type { APIRequestContext, Page } from '@playwright/test'
import { API_BASE, apiPostJson, expect, isRecord, test, type CsrfHeaders } from '../fixtures'
import { approveExecuteInSkill, sendMessage, setupLangGraphV3Agent } from '../langgraph-v3-helpers'
import { capture, captureLocator, DESKTOP_VIEWPORT, settle, warmUpChatRoute } from './_capture-helpers'

/**
 * Wave 10 — HITL approval DECISION states. The existing chat-state matrix
 * (wave4) only captures the initial approval cards (single + multi). This spec
 * drives each decision to its result and captures the screen changes from the
 * HITL edit-hardening work:
 *
 *  - allowed_decisions gating: execute_in_skill ([approve, reject]) shows NO 수정
 *    button, while edit_file ([approve, edit, reject]) DOES — the visible contrast.
 *  - the field-based editor that replaced the raw JSON textarea, with the
 *    sensitive key locked read-only (<redacted>).
 *  - approve / reject / edit result badges (승인됨 / 거부됨 / 수정 승인됨).
 *
 * Split into two tests so the multi-action pair gets its own timeout budget (the
 * single+edit set alone spends most of one). Each scenario runs in a FRESH
 * conversation for isolation; best-effort per item so one failing scenario never
 * drops the rest. The CARD (data-testid="approval-action-N") is element-scoped so
 * the shot is just the card; after a decision the card becomes a badge with no
 * testid, so badge/multi states fall back to a full-page screenshot of the (short)
 * conversation. Gated by E2E_CAPTURE_TOUR=1.
 */

const WAVE = 'wave10-hitl-decisions'
const SINGLE_PROMPT = '문서 생성 도구를 사용해 승인 후 실행해줘'

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

interface ChatDriver {
  readonly card: ReturnType<Page['locator']>
  readonly sendAndWaitCard: (title: string, prompt: string) => Promise<void>
}

function makeChatDriver(
  page: Page,
  request: APIRequestContext,
  agentId: string,
  csrfHeaders: CsrfHeaders,
): ChatDriver {
  const card = page.locator('[data-testid^="approval-action-"]')

  const gotoChat = async (conversationId: string): Promise<void> => {
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

  const sendAndWaitCard = async (title: string, prompt: string): Promise<void> => {
    const conversationId = await freshConversation(request, csrfHeaders, agentId, title)
    await gotoChat(conversationId)
    await sendMessage(page, prompt)
    await expect(page.getByText(/승인이 필요합니다|Approval Required/).last()).toBeVisible({
      timeout: 40_000,
    })
    await page.waitForTimeout(600)
  }

  return { card, sendAndWaitCard }
}

async function runSteps(
  steps: ReadonlyArray<{ readonly file: string; readonly run: () => Promise<void> }>,
): Promise<void> {
  for (const step of steps) {
    try {
      await step.run()
    } catch (error) {
      console.warn(`[capture-tour] hitl-decision ${step.file} failed: ${String(error)}`)
    }
  }
}

test.describe('Wave 10 — HITL approval decision captures', () => {
  test.skip(process.env.E2E_CAPTURE_TOUR !== '1', 'Set E2E_CAPTURE_TOUR=1 to run the capture tour')

  test.beforeAll(async ({ browser }) => {
    test.setTimeout(300_000)
    await warmUpChatRoute(browser)
  })

  test('single + edit decision states', async ({ page, request }) => {
    test.setTimeout(600_000)
    await page.setViewportSize(DESKTOP_VIEWPORT)
    const setup = await setupLangGraphV3Agent(request)
    const { parentAgentId: agentId, childAgentId, csrfHeaders } = setup
    const { card, sendAndWaitCard } = makeChatDriver(page, request, agentId, csrfHeaders)

    try {
      await runSteps([
        // ── Single (execute_in_skill → [approve, reject], NO 수정 button) ──
        {
          file: '01-single-card.png',
          run: async () => {
            await sendAndWaitCard('HITL 단독 카드', SINGLE_PROMPT)
            await captureLocator(card.last(), WAVE, '01-single-card.png')
          },
        },
        {
          file: '02-single-approved.png',
          run: async () => {
            await sendAndWaitCard('HITL 단독 승인', SINGLE_PROMPT)
            await approveExecuteInSkill(page)
            await expect(page.getByText('승인됨', { exact: true }).last()).toBeVisible({
              timeout: 30_000,
            })
            await page.waitForTimeout(400)
            await capture(page, WAVE, '02-single-approved.png')
          },
        },
        {
          file: '03-single-rejected.png',
          run: async () => {
            await sendAndWaitCard('HITL 단독 거부', SINGLE_PROMPT)
            await page.getByRole('button', { name: '거부', exact: true }).last().click()
            await page.getByRole('button', { name: '거부 확인' }).last().click()
            await expect(page.getByText('거부됨', { exact: true }).last()).toBeVisible({
              timeout: 30_000,
            })
            await page.waitForTimeout(400)
            await capture(page, WAVE, '03-single-rejected.png')
          },
        },
        // ── Edit-capable (edit_file → [approve, edit, reject], 수정 button shown) ──
        {
          file: '04-edit-card.png',
          run: async () => {
            await sendAndWaitCard('HITL 수정 카드', 'E2E_HITL_EDIT')
            await captureLocator(card.last(), WAVE, '04-edit-card.png')
          },
        },
        {
          file: '05-edit-field-editor.png',
          run: async () => {
            await sendAndWaitCard('HITL field editor', 'E2E_HITL_EDIT')
            await page.getByRole('button', { name: '수정', exact: true }).last().click()
            // One control per arg; the sensitive key (api_key) is locked read-only.
            await expect(page.getByLabel('file_path').last()).toBeVisible({ timeout: 10_000 })
            await expect(page.getByLabel('api_key').last()).toBeDisabled()
            await page.waitForTimeout(400)
            await captureLocator(card.last(), WAVE, '05-edit-field-editor.png')
          },
        },
        {
          file: '06-edit-approved.png',
          run: async () => {
            await sendAndWaitCard('HITL 수정 후 승인', 'E2E_HITL_EDIT')
            await page.getByRole('button', { name: '수정', exact: true }).last().click()
            await expect(page.getByLabel('new_string').last()).toBeVisible({ timeout: 10_000 })
            // Edit a non-secret field to show the editor is interactive.
            await page.getByLabel('new_string').last().fill('region: eu-west-1')
            await page.getByRole('button', { name: '수정 후 승인' }).last().click()
            await expect(page.getByText('수정 승인됨', { exact: true }).last()).toBeVisible({
              timeout: 30_000,
            })
            await page.waitForTimeout(400)
            await capture(page, WAVE, '06-edit-approved.png')
          },
        },
      ])
    } finally {
      await settle(page, 200)
      await request
        .delete(`${API_BASE}/api/agents/${agentId}`, { headers: csrfHeaders })
        .catch(() => {})
      await request
        .delete(`${API_BASE}/api/agents/${childAgentId}`, { headers: csrfHeaders })
        .catch(() => {})
    }
  })

  test('multi-action decision states', async ({ page, request }) => {
    test.setTimeout(600_000)
    await page.setViewportSize(DESKTOP_VIEWPORT)
    const setup = await setupLangGraphV3Agent(request)
    const { parentAgentId: agentId, childAgentId, csrfHeaders } = setup
    const { card } = makeChatDriver(page, request, agentId, csrfHeaders)
    const group = page.getByTestId('approval-group')

    const gotoSendMulti = async (title: string): Promise<void> => {
      const conversationId = await freshConversation(request, csrfHeaders, agentId, title)
      for (let attempt = 1; attempt <= 2; attempt += 1) {
        try {
          await page.goto(`/agents/${agentId}/conversations/${conversationId}`, {
            waitUntil: 'domcontentloaded',
            timeout: 180_000,
          })
          break
        } catch (error) {
          if (attempt === 2) throw error
          await page.waitForTimeout(2_000)
        }
      }
      await sendMessage(page, 'E2E_HITL_MULTI')
      // Both grouped-compact and standalone cards carry approval-action-N, so
      // wait on that (not the header text, which differs between the two modes).
      await expect(card.first()).toBeVisible({ timeout: 40_000 })
      await page.waitForTimeout(600)
    }

    try {
      await runSteps([
        {
          file: '07-multi-card.png',
          run: async () => {
            await gotoSendMulti('HITL 멀티 카드')
            // ONE grouped container ("승인 대기 N건" + "모두 승인") wrapping the two
            // compact action rows — not two standalone cards.
            await expect(group).toBeVisible({ timeout: 20_000 })
            await expect(card).toHaveCount(2, { timeout: 20_000 })
            await page.waitForTimeout(400)
            await captureLocator(group, WAVE, '07-multi-card.png')
          },
        },
        {
          file: '08-multi-approved.png',
          run: async () => {
            await gotoSendMulti('HITL 멀티 승인')
            // One "모두 승인" click drives every action; the coordinator resumes once.
            await page.getByTestId('approval-approve-all-button').click()
            await expect(page.getByText('승인됨', { exact: true })).toHaveCount(2, {
              timeout: 30_000,
            })
            await page.waitForTimeout(400)
            await capture(page, WAVE, '08-multi-approved.png')
          },
        },
      ])
    } finally {
      await settle(page, 200)
      await request
        .delete(`${API_BASE}/api/agents/${agentId}`, { headers: csrfHeaders })
        .catch(() => {})
      await request
        .delete(`${API_BASE}/api/agents/${childAgentId}`, { headers: csrfHeaders })
        .catch(() => {})
    }
  })
})
