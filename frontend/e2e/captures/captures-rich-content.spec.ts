import type { Page } from '@playwright/test'
import { API_BASE, loginApi, test } from '../fixtures'
import { sendMessage, waitForActiveRun, approveExecuteInSkill } from '../langgraph-v3-helpers'
import {
  addIntervalTrigger,
  capture,
  createConfiguredAgent,
  createConversation,
  createRichAgent,
  deleteAgents,
  DESKTOP_VIEWPORT,
  scriptedModelId,
  seedRealisticAgents,
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
    const { agentId, childId } = await createConfiguredAgent(request, csrf)
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

  test('dashboard — agent list + sort menu', async ({ page, request }) => {
    test.setTimeout(180_000)
    const csrf = await loginApi(request)
    // Seed several agents + a few sessions (via API) so the grid + sidebar list
    // look populated. NO conversation navigation (the chat route's cold compile
    // was blowing the test budget) — the priority is the sort MENU.
    const agentIds = await seedRealisticAgents(request, csrf)
    if (agentIds[0]) {
      for (const title of ['멤버십 취소 문의', '수업 예약 도움', '크레딧 잔액 확인']) {
        await createConversation(request, csrf, agentIds[0], title)
      }
    }
    try {
      await nav(page, '/')
      await settle(page, 1_500)
      await capture(page, WAVE, '11-dashboard-sessions.png')

      // Dashboard grid sort control — a dropdown trigger labeled with the current
      // sort (최신순). Open it to reveal the menu (최신순 / 이름순 / 즐겨찾기).
      const sortTrigger = page.getByRole('button', { name: /최신순|이름순|즐겨찾기/ }).first()
      if ((await sortTrigger.count()) > 0) {
        await sortTrigger.click().catch(() => {})
      } else {
        await page.getByText(/최신순/).first().click().catch(() => {})
      }
      await page.getByRole('menuitem').first().waitFor({ state: 'visible', timeout: 8_000 }).catch(() => {})
      await page.waitForTimeout(500)
      await capture(page, WAVE, '12-dashboard-sort.png')
    } finally {
      await deleteAgents(request, csrf, agentIds)
    }
  })

  test('schedules — registered trigger', async ({ page, request }) => {
    test.setTimeout(180_000)
    const csrf = await loginApi(request)
    // Triggers require a fixed-identity agent on the openai_compatible model.
    const { agentId, childId } = await createConfiguredAgent(request, csrf)
    await addIntervalTrigger(request, csrf, agentId, 30)
    try {
      await nav(page, '/settings/schedules')
      await settle(page, 1_500)
      await capture(page, WAVE, '13-schedules-registered.png')
    } finally {
      await request.delete(`${API_BASE}/api/agents/${agentId}`, { headers: csrf }).catch(() => {})
      if (childId) await request.delete(`${API_BASE}/api/agents/${childId}`, { headers: csrf }).catch(() => {})
    }
  })

  test('files — attachment (composer + bubble), list, image lightbox', async ({ page, request }) => {
    test.setTimeout(300_000)
    const csrf = await loginApi(request)
    // Attachment flow on a SIMPLE scripted agent (matches the proven
    // chat-attachments-display flow — no skill to perturb the inline render).
    const simpleModelId = await scriptedModelId(request)
    const simpleCreated = await request.post(`${API_BASE}/api/agents`, {
      headers: csrf,
      data: { name: '첨부 도우미', system_prompt: '첨부를 확인합니다.', model_id: simpleModelId },
    })
    const simpleAgent = (await simpleCreated.json()) as { id: string }
    try {
      const cid = await createConversation(request, csrf, simpleAgent.id, '첨부 데모')
      await nav(page, `/agents/${simpleAgent.id}/conversations/${cid}`)

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
      // Match the proven chat-attachments-display flow: wait for the scripted
      // reply, then the inline image (looked up by message id from /files).
      await page
        .getByText('E2E scripted document model is ready.')
        .first()
        .waitFor({ state: 'visible', timeout: 60_000 })
        .catch(() => {})
      const userMsg = page.locator('[data-moldy-message-role="user"]').last()
      const inlineImg = userMsg.getByRole('img', { name: 'membership-card.png' })
      await inlineImg.waitFor({ state: 'visible', timeout: 25_000 }).catch(() => {})
      await page.waitForTimeout(500)
      await capture(page, WAVE, '15-attachment-bubble.png')

      // Click the inline attachment image → lightbox (image enlarge).
      if ((await inlineImg.count()) > 0) {
        await inlineImg.first().click().catch(() => {})
        await page.waitForTimeout(900)
        await capture(page, WAVE, '17-image-lightbox.png')
        await page.keyboard.press('Escape').catch(() => {})
      }

      // Generate a real artifact (skill agent) so the Files (생성된 파일) list isn't empty.
      const { agentId: skillAgentId, childId } = await createRichAgent(request, csrf)
      try {
        const cid2 = await createConversation(request, csrf, skillAgentId, '문서 생성')
        await nav(page, `/agents/${skillAgentId}/conversations/${cid2}`)
        await sendMessage(page, 'E2E_DOCX 문서를 생성해줘')
        await approveExecuteInSkill(page).catch(() => {})
        await settleStream(page, 120_000)
        await nav(page, '/artifacts')
        await settle(page, 1_500)
        await capture(page, WAVE, '16-file-list.png')
      } finally {
        await request.delete(`${API_BASE}/api/agents/${skillAgentId}`, { headers: csrf }).catch(() => {})
        if (childId) await request.delete(`${API_BASE}/api/agents/${childId}`, { headers: csrf }).catch(() => {})
      }
    } finally {
      await request.delete(`${API_BASE}/api/agents/${simpleAgent.id}`, { headers: csrf }).catch(() => {})
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
      await settle(page, 1_800)
      // Expand the tree so child spans are clickable.
      await page.getByRole('button', { name: /전체 펼치기|모두 펼치기|펼치기|expand/i }).first().click().catch(() => {})
      await page.waitForTimeout(600)
      // The default selection is the root span (no conversation). Select a
      // child LLM/agent span so the detail panel shows the actual messages.
      const selectSpan = async (pattern: RegExp): Promise<boolean> => {
        const node = page.getByText(pattern).first()
        if ((await node.count()) > 0 && (await node.isVisible().catch(() => false))) {
          await node.click().catch(() => {})
          await page.waitForTimeout(800)
          return true
        }
        return false
      }
      ;(await selectSpan(/ChatModel|chat_model|LLM|model|에이전트|agent|LangGraph|call_model/i)) ||
        (await selectSpan(/current_datetime|resolve_relative|tool|도구/i))
      await capture(page, WAVE, '18-trace.png')
      // Select a different (tool/result) span for the detail view.
      await selectSpan(/current_datetime|resolve_relative|tool|output|결과|result/i)
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
