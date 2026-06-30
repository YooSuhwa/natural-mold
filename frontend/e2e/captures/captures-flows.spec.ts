import type { APIRequestContext } from '@playwright/test'
import { API_BASE, apiPostJson, isRecord, loginApi, test, type CsrfHeaders } from '../fixtures'
import { sendMessage } from '../langgraph-v3-helpers'
import { capture, DESKTOP_VIEWPORT, scriptedModelId, settle } from './_capture-helpers'

/**
 * Wave 1 — hero flows (the two demo videos): natural-language agent creation
 * (builder) and a multi-turn daily conversation exercising chat components.
 * Step-by-step captures. Gated by E2E_CAPTURE_TOUR=1.
 */

const WAVE = 'wave1-flows'

async function createAgent(
  request: APIRequestContext,
  csrfHeaders: CsrfHeaders,
  modelId: string,
): Promise<string> {
  const agent = await apiPostJson(request, `${API_BASE}/api/agents`, csrfHeaders, {
    name: '지피 — 일상 비서',
    description: '일정·정보·추천을 도와주는 개인 비서',
    system_prompt: '당신은 친근한 개인 비서입니다. 사용자의 일상 질문에 간결하고 도움이 되게 답합니다.',
    model_id: modelId,
  })
  if (!isRecord(agent) || typeof agent.id !== 'string') throw new Error('agent create failed')
  return agent.id
}

test.describe('Wave 1 — hero flow captures', () => {
  test.skip(process.env.E2E_CAPTURE_TOUR !== '1', 'Set E2E_CAPTURE_TOUR=1 to run the capture tour')

  test.beforeEach(async ({ page }) => {
    await page.setViewportSize(DESKTOP_VIEWPORT)
  })

  test('daily conversation — multi-turn chat components', async ({ page, request }) => {
    test.setTimeout(240_000)
    const csrfHeaders = await loginApi(request)
    const modelId = await scriptedModelId(request)
    const agentId = await createAgent(request, csrfHeaders, modelId)

    try {
      const convo = await apiPostJson(
        request,
        `${API_BASE}/api/agents/${agentId}/conversations`,
        csrfHeaders,
        { title: '오늘의 비서 대화' },
      )
      if (!isRecord(convo) || typeof convo.id !== 'string') throw new Error('conversation failed')

      // Cold-compile tolerant first navigation.
      for (let attempt = 1; attempt <= 2; attempt += 1) {
        try {
          await page.goto(`/agents/${agentId}/conversations/${convo.id}`, {
            waitUntil: 'domcontentloaded',
            timeout: 180_000,
          })
          break
        } catch (error) {
          if (attempt === 2) throw error
          await page.waitForTimeout(2_000)
        }
      }
      await settle(page)
      await capture(page, WAVE, '01-empty-greeting.png')

      const settleStream = async (): Promise<void> => {
        const stop = page.locator('[data-moldy-stop-button="true"]:visible').last()
        await stop.waitFor({ state: 'visible', timeout: 8_000 }).catch(() => {})
        await stop.waitFor({ state: 'hidden', timeout: 90_000 }).catch(() => {})
        await page.waitForTimeout(800)
      }

      // Turn 1 — a rich, formatted answer (natural-language trigger).
      await sendMessage(
        page,
        '이번 주 홈트 루틴을 체크리스트, 표, 코드, 수식, 이미지, 링크, 인용문, Mermaid 다이어그램으로 정리해줘',
      )
      await settleStream()
      await page.locator('svg').first().waitFor({ state: 'visible', timeout: 12_000 }).catch(() => {})
      await capture(page, WAVE, '02-rich-answer.png')

      // Turn 2 — an interactive ask_user card (natural-language trigger).
      await sendMessage(page, '운동 후 간식을 ask_user로 사과, 포도, 배 중에 골라줘')
      await page
        .getByText(/어떤 과일이 좋아요|🍎 사과|입력이 필요합니다/)
        .last()
        .waitFor({ state: 'visible', timeout: 30_000 })
        .catch(() => {})
      await page.waitForTimeout(600)
      await capture(page, WAVE, '03-ask-user.png')

      // Answer the option → the conversation continues.
      await page.getByRole('button', { name: /사과/ }).first().click().catch(() => {})
      await settleStream()
      await capture(page, WAVE, '04-after-selection.png')
    } finally {
      await request.delete(`${API_BASE}/api/agents/${agentId}`, { headers: csrfHeaders }).catch(() => {})
    }
  })

  test('agent creation — conversational builder flow', async ({ page }) => {
    test.setTimeout(180_000)
    const prompt = '헬스장 멤버십 문의에 답하고 예약·취소를 돕는 고객지원 봇을 만들어줘'

    for (let attempt = 1; attempt <= 2; attempt += 1) {
      try {
        await page.goto(`/agents/new/conversational?initialMessage=${encodeURIComponent(prompt)}`, {
          waitUntil: 'domcontentloaded',
          timeout: 180_000,
        })
        break
      } catch (error) {
        if (attempt === 2) throw error
        await page.waitForTimeout(2_000)
      }
    }
    await page.getByText(/세션 #/).waitFor({ state: 'visible', timeout: 40_000 }).catch(() => {})
    await capture(page, WAVE, '05-builder-welcome.png')

    // Let the builder stream its response (or surface a System LLM error state).
    const stop = page.locator('[data-moldy-stop-button="true"]:visible').last()
    await stop.waitFor({ state: 'visible', timeout: 15_000 }).catch(() => {})
    await stop.waitFor({ state: 'hidden', timeout: 90_000 }).catch(() => {})
    await page.waitForTimeout(1_500)
    await capture(page, WAVE, '06-builder-result.png')
  })
})
