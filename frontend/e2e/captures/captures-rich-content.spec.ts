import type { Page } from '@playwright/test'
import { API_BASE, loginApi, test } from '../fixtures'
import { sendMessage, waitForActiveRun, approveExecuteInSkill } from '../langgraph-v3-helpers'
import {
  addIntervalTrigger,
  capture,
  createConversation,
  createRichAgent,
  DESKTOP_VIEWPORT,
  scriptedModelId,
  settle,
  TINY_PNG_BASE64,
} from './_capture-helpers'

/**
 * Wave 7 — content-rich / new captures (user feedback): agent edit tabs + visual,
 * dashboard agent-expand sessions + sort, schedules with a registered trigger,
 * file list + attachment (composer + bubble) + image lightbox, trace view, usage.
 * Each area is its own test (isolation). Best-effort. Gated by E2E_CAPTURE_TOUR=1.
 */

const WAVE = 'wave7-rich-content'

async function nav(page: Page, url: string): Promise<void> {
  for (let attempt = 1; attempt <= 2; attempt += 1) {
    try {
      await page.goto(url, { waitUntil: 'domcontentloaded', timeout: 150_000 })
      return
    } catch (error) {
      if (attempt === 2) throw error
      await page.waitForTimeout(2_000)
    }
  }
}

async function settleStream(page: Page, timeout = 90_000): Promise<void> {
  const stop = page.locator('[data-moldy-stop-button="true"]:visible').last()
  await stop.waitFor({ state: 'visible', timeout: 8_000 }).catch(() => {})
  await stop.waitFor({ state: 'hidden', timeout }).catch(() => {})
  await page.waitForTimeout(800)
}

async function clickTab(page: Page, name: RegExp): Promise<boolean> {
  for (const role of ['tab', 'button', 'link'] as const) {
    const el = page.getByRole(role, { name }).first()
    if ((await el.count()) > 0 && (await el.isVisible().catch(() => false))) {
      await el.click().catch(() => {})
      await page.waitForTimeout(600)
      return true
    }
  }
  return false
}

test.describe('Wave 7 — rich content captures', () => {
  test.skip(process.env.E2E_CAPTURE_TOUR !== '1', 'Set E2E_CAPTURE_TOUR=1 to run the capture tour')

  test.beforeEach(async ({ page }) => {
    await page.setViewportSize(DESKTOP_VIEWPORT)
  })

  test('agent edit — tabs with content + visual', async ({ page, request }) => {
    test.setTimeout(240_000)
    const csrf = await loginApi(request)
    const { agentId, childId } = await createRichAgent(request, csrf)
    await addIntervalTrigger(request, csrf, agentId, 30)
    try {
      await nav(page, `/agents/${agentId}/settings`)
      await settle(page)
      await capture(page, WAVE, '01-edit-overview.png')

      // Right-panel tabs (Fix/테스트/오프너/스케줄/설정/API) + left form's 비주얼 toggle.
      const tabs: ReadonlyArray<readonly [RegExp, string]> = [
        [/비주얼/, '02-edit-form-visual.png'],
        [/테스트/, '03-edit-test.png'],
        [/오프너/, '04-edit-opener.png'],
        [/스케줄/, '05-edit-schedule.png'],
        [/^설정$/, '06-edit-settings.png'],
        [/^API$/, '07-edit-api.png'],
      ]
      for (const [name, file] of tabs) {
        if (await clickTab(page, name)) await capture(page, WAVE, file)
      }

      await nav(page, `/agents/${agentId}/visual-settings`)
      await settle(page, 1_500)
      await capture(page, WAVE, '10-edit-visual.png')
    } finally {
      await request.delete(`${API_BASE}/api/agents/${agentId}`, { headers: csrf }).catch(() => {})
      if (childId) await request.delete(`${API_BASE}/api/agents/${childId}`, { headers: csrf }).catch(() => {})
    }
  })

  test('dashboard — agent expanded sessions + sort', async ({ page, request }) => {
    test.setTimeout(240_000)
    const csrf = await loginApi(request)
    const modelId = await scriptedModelId(request)
    const created = await request.post(`${API_BASE}/api/agents`, {
      headers: csrf,
      data: {
        name: '핏라이프 멤버십 지원봇',
        description: '헬스장 멤버십 문의·예약·취소 고객지원',
        system_prompt: '고객지원 상담원입니다.',
        model_id: modelId,
      },
    })
    const agent = (await created.json()) as { id: string }
    try {
      let lastCid = ''
      for (const title of ['멤버십 취소 문의', '수업 예약 도움', '크레딧 잔액 확인']) {
        lastCid = await createConversation(request, csrf, agent.id, title)
      }
      // Open a conversation → the sidebar shows the agent expanded with its full
      // session list (the "agent opened, sessions visible" view).
      await nav(page, `/agents/${agent.id}/conversations/${lastCid}`)
      await settle(page, 1_200)
      await capture(page, WAVE, '11-dashboard-sessions.png')

      // Dashboard grid sort control (opens a sort menu: 최신순 / 이름순 …).
      await nav(page, '/')
      await settle(page, 1_000)
      const sort = page.getByText(/최신순|이름순/).first()
      if ((await sort.count()) > 0) {
        await sort.click().catch(() => {})
        await page.waitForTimeout(700)
        await capture(page, WAVE, '12-dashboard-sort.png')
      }
    } finally {
      await request.delete(`${API_BASE}/api/agents/${agent.id}`, { headers: csrf }).catch(() => {})
    }
  })

  test('schedules — registered trigger', async ({ page, request }) => {
    test.setTimeout(180_000)
    const csrf = await loginApi(request)
    const modelId = await scriptedModelId(request)
    const created = await request.post(`${API_BASE}/api/agents`, {
      headers: csrf,
      data: { name: '일일 리포트 봇', system_prompt: '리포트 작성.', model_id: modelId },
    })
    const agent = (await created.json()) as { id: string }
    await addIntervalTrigger(request, csrf, agent.id, 30)
    try {
      await nav(page, '/settings/schedules')
      await settle(page, 1_200)
      await capture(page, WAVE, '13-schedules-registered.png')
    } finally {
      await request.delete(`${API_BASE}/api/agents/${agent.id}`, { headers: csrf }).catch(() => {})
    }
  })

  test('files — attachment (composer + bubble), list, image lightbox', async ({ page, request }) => {
    test.setTimeout(300_000)
    const csrf = await loginApi(request)
    const { agentId, childId } = await createRichAgent(request, csrf) // has docx skill
    try {
      const cid = await createConversation(request, csrf, agentId, '첨부 데모')
      await nav(page, `/agents/${agentId}/conversations/${cid}`)

      // Attach an image in the composer → capture the staged chip.
      const [chooser] = await Promise.all([
        page.waitForEvent('filechooser'),
        page.getByRole('button', { name: '파일 첨부' }).click(),
      ])
      await chooser.setFiles({
        name: 'membership-card.png',
        mimeType: 'image/png',
        buffer: Buffer.from(TINY_PNG_BASE64, 'base64'),
      })
      await page.getByText('membership-card.png').first().waitFor({ state: 'visible', timeout: 10_000 }).catch(() => {})
      await capture(page, WAVE, '14-attachment-composer.png')

      // Send → the inline attachment thumbnail appears on the user bubble once
      // the run completes and the /files query refetches (message_id backfill).
      const uploadDone = page
        .waitForResponse(
          (r) => r.url().includes('/api/uploads') && r.request().method() === 'POST',
          { timeout: 30_000 },
        )
        .catch(() => null)
      await page.getByPlaceholder('메시지 입력...').fill('이 회원증 이미지 확인해줘')
      await page.getByRole('button', { name: /전송/ }).click().catch(() => {})
      await uploadDone
      await settleStream(page)
      const userMsg = page.locator('[data-moldy-message-role="user"]').last()
      const inlineImg = userMsg.getByRole('img', { name: 'membership-card.png' })
      await inlineImg.waitFor({ state: 'visible', timeout: 30_000 }).catch(() => {})
      await page.waitForTimeout(500)
      await capture(page, WAVE, '15-attachment-bubble.png')

      // Click the inline attachment image → lightbox (image enlarge).
      if ((await inlineImg.count()) > 0) {
        await inlineImg.first().click().catch(() => {})
        await page.waitForTimeout(900)
        await capture(page, WAVE, '17-image-lightbox.png')
        await page.keyboard.press('Escape').catch(() => {})
      }

      // Generate a real artifact so the Files (생성된 파일) list isn't empty.
      const cid2 = await createConversation(request, csrf, agentId, '문서 생성')
      await nav(page, `/agents/${agentId}/conversations/${cid2}`)
      await sendMessage(page, 'E2E_DOCX 문서를 생성해줘')
      await approveExecuteInSkill(page).catch(() => {})
      await settleStream(page, 120_000)
      await nav(page, '/artifacts')
      await settle(page, 1_500)
      await capture(page, WAVE, '16-file-list.png')
    } finally {
      await request.delete(`${API_BASE}/api/agents/${agentId}`, { headers: csrf }).catch(() => {})
      if (childId) await request.delete(`${API_BASE}/api/agents/${childId}`, { headers: csrf }).catch(() => {})
    }
  })

  test('trace — run trace view', async ({ page, request }) => {
    test.setTimeout(180_000)
    const csrf = await loginApi(request)
    const modelId = await scriptedModelId(request)
    const created = await request.post(`${API_BASE}/api/agents`, {
      headers: csrf,
      data: { name: '트레이스 데모', system_prompt: '도움.', model_id: modelId },
    })
    const agent = (await created.json()) as { id: string }
    try {
      const cid = await createConversation(request, csrf, agent.id, '트레이스 대화')
      await nav(page, `/agents/${agent.id}/conversations/${cid}`)
      await sendMessage(page, 'E2E_TOOL_GROUP')
      await waitForActiveRun(request, cid).catch(() => {})
      await settleStream(page)
      await nav(page, `/agents/${agent.id}/conversations/${cid}/traces`)
      await settle(page, 1_500)
      await capture(page, WAVE, '18-trace.png')
      // Expand the first trace node to reveal the conversation/tool detail.
      await page.locator('[role="button"], button').filter({ hasText: /./ }).first().click().catch(() => {})
      await page.waitForTimeout(800)
      await capture(page, WAVE, '19-trace-detail.png')
    } finally {
      await request.delete(`${API_BASE}/api/agents/${agent.id}`, { headers: csrf }).catch(() => {})
    }
  })

  test('usage — with recorded data', async ({ page, request }) => {
    test.setTimeout(180_000)
    const csrf = await loginApi(request)
    const modelId = await scriptedModelId(request)
    const created = await request.post(`${API_BASE}/api/agents`, {
      headers: csrf,
      data: { name: '사용량 데모', system_prompt: '응답.', model_id: modelId },
    })
    const agent = (await created.json()) as { id: string }
    try {
      const cid = await createConversation(request, csrf, agent.id, '사용량 대화')
      await nav(page, `/agents/${agent.id}/conversations/${cid}`)
      for (let i = 0; i < 3; i += 1) {
        await sendMessage(page, 'E2E_TOKEN_USAGE_STREAM')
        await settleStream(page)
      }
      await nav(page, '/usage')
      await settle(page, 1_500)
      await capture(page, WAVE, '20-usage.png')
    } finally {
      await request.delete(`${API_BASE}/api/agents/${agent.id}`, { headers: csrf }).catch(() => {})
    }
  })
})
