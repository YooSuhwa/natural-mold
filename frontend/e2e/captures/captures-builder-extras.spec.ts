import type { Page } from '@playwright/test'
import { API_BASE, apiPostJson, isRecord, loginApi, test, type CsrfHeaders } from '../fixtures'
import {
  capture,
  createConversation,
  deleteAgents,
  DESKTOP_VIEWPORT,
  scriptedModelId,
  seedRealisticAgents,
  settle,
} from './_capture-helpers'

/**
 * Wave 8 (feedback): sidebar navigator sort/view menu, the opener-question empty
 * chat state, and the conversational builder captured step-by-step (incl. the
 * image-generation phase). Gated by E2E_CAPTURE_TOUR=1.
 */

const WAVE = 'wave8-builder-extras'

async function nav(page: Page, url: string): Promise<void> {
  for (let attempt = 1; attempt <= 2; attempt += 1) {
    try {
      await page.goto(url, { waitUntil: 'domcontentloaded', timeout: 180_000 })
      return
    } catch (error) {
      if (attempt === 2) throw error
      await page.waitForTimeout(2_000)
    }
  }
}

async function streamSettle(page: Page, timeout = 90_000): Promise<void> {
  const stop = page.locator('[data-moldy-stop-button="true"]:visible').last()
  await stop.waitFor({ state: 'visible', timeout: 6_000 }).catch(() => {})
  await stop.waitFor({ state: 'hidden', timeout }).catch(() => {})
  await page.waitForTimeout(700)
}

test.describe('Wave 8 — builder + extras captures', () => {
  test.skip(process.env.E2E_CAPTURE_TOUR !== '1', 'Set E2E_CAPTURE_TOUR=1 to run the capture tour')

  test.beforeEach(async ({ page }) => {
    await page.setViewportSize(DESKTOP_VIEWPORT)
  })

  test('sidebar navigator — sort + view-options menu', async ({ page, request }) => {
    test.setTimeout(180_000)
    const csrf = await loginApi(request)
    const agentIds = await seedRealisticAgents(request, csrf)
    if (agentIds[0]) {
      for (const title of ['멤버십 취소 문의', '수업 예약 도움']) {
        await createConversation(request, csrf, agentIds[0], title)
      }
    }
    try {
      await nav(page, '/')
      await settle(page, 1_200)
      // The navigator options live behind the "탐색 옵션" (⋯) button in the
      // sidebar agent-list header (alongside + and search).
      const trigger = page.getByRole('button', { name: '탐색 옵션' }).first()
      await trigger.click().catch(() => {})
      await page.getByRole('menu').first().waitFor({ state: 'visible', timeout: 8_000 }).catch(() => {})
      await page.waitForTimeout(400)
      await capture(page, WAVE, '01-sidebar-navigator-menu.png')
      // Open the "보기 방식" submenu to reveal the view modes (에이전트별 / 최근 에이전트 / 최근 대화).
      await page.getByText('보기 방식').first().hover().catch(() => {})
      await page.waitForTimeout(700)
      await capture(page, WAVE, '02-sidebar-view-modes.png')
    } finally {
      await deleteAgents(request, csrf, agentIds)
    }
  })

  test('opener questions — empty chat state', async ({ page, request }) => {
    test.setTimeout(300_000)
    const csrf = await loginApi(request)
    const modelId = await scriptedModelId(request)
    const created = await apiPostJson(request, `${API_BASE}/api/agents`, csrf, {
      name: '핏라이프 멤버십 지원봇',
      description: '헬스장 멤버십 문의·예약·취소 고객지원',
      system_prompt: '고객지원 상담원입니다.',
      model_id: modelId,
      opener_questions: [
        '멤버십 크레딧이 얼마나 남았는지 알려줘',
        '이번 주 요가 수업을 예약하고 싶어',
        '멤버십을 취소하려면 어떻게 해?',
      ],
    })
    const agentId = isRecord(created) && typeof created.id === 'string' ? created.id : ''
    try {
      const cid = await createConversation(request, csrf, agentId, '새 대화')
      // 'commit' returns as soon as the navigation commits (avoids the
      // domcontentloaded/networkidle hang seen on the chat route); then wait for
      // the empty state's opener questions to render.
      await page
        .goto(`/agents/${agentId}/conversations/${cid}`, { waitUntil: 'commit', timeout: 120_000 })
        .catch(() => {})
      await page
        .getByText(/멤버십 크레딧이 얼마나|요가 수업을 예약|멤버십을 취소/)
        .first()
        .waitFor({ state: 'visible', timeout: 90_000 })
        .catch(() => {})
      await page.waitForTimeout(1_500)
      await capture(page, WAVE, '03-opener-empty-state.png')
    } finally {
      await deleteAgents(request, csrf, [agentId])
    }
  })

  test('conversational builder — step by step', async ({ page }) => {
    test.setTimeout(360_000)
    const prompt = '헬스장 멤버십 문의에 답하고 예약·취소를 돕는 고객지원 봇을 만들어줘'
    await nav(page, `/agents/new/conversational?initialMessage=${encodeURIComponent(prompt)}`)
    await page.getByText(/세션 #/).waitFor({ state: 'visible', timeout: 40_000 }).catch(() => {})
    await capture(page, WAVE, '10-builder-welcome.png')

    // Verified via diagnostics: options are <button role="option">; selecting the
    // LAST one (freshest card if regenerated) enables the EXACT 다음/완료 button.
    // Must NOT match "재생성" (regenerate) — that loops the card forever.
    let dryRounds = 0
    for (let step = 1; step <= 16 && dryRounds < 3; step += 1) {
      await streamSettle(page, 60_000)
      await page.waitForTimeout(1_200)
      await capture(page, WAVE, `11-builder-step-${String(step).padStart(2, '0')}.png`)

      const option = page.locator('[role="option"]').last()
      if ((await option.count()) > 0 && (await option.isVisible().catch(() => false))) {
        await option.click().catch(() => {})
        await page.waitForTimeout(700)
      }

      // Each phase's advance differs: the intent wizard uses 다음/완료; later
      // phases (tool/middleware/prompt/image/save/build) use 승인하고 진행. Never
      // 재생성 / 수정 요청.
      const next = page
        .getByRole('button', { name: /^(다음|완료|제출|확인|승인하고 진행|승인|진행|건너뛰기|시작하기)$/ })
        .last()
      if (
        (await next.count()) > 0 &&
        (await next.isVisible().catch(() => false)) &&
        (await next.isEnabled().catch(() => false))
      ) {
        await next.click().catch(() => {})
        dryRounds = 0
        await page.waitForTimeout(3_000)
      } else {
        // No actionable card — the phase is processing (real LLM). Wait and retry.
        dryRounds += 1
        await page.waitForTimeout(4_000)
      }
    }

    await streamSettle(page, 90_000)
    await capture(page, WAVE, '20-builder-final.png')
  })
})
