import type { Page } from '@playwright/test'
import { API_BASE, apiGetJson, isRecord, loginApi, test } from '../fixtures'
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

      // Dashboard grid sort control — a dropdown trigger (icon + current-sort
      // label). Targeted by testid because the label is icon-adjacent inside a
      // Base UI render-prop trigger, and once open the same strings appear as
      // menuitems (so getByText/role-name was ambiguous and unreliable).
      await page.getByTestId('dashboard-sort-trigger').click().catch(() => {})
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

      // Mirror the proven chat-attachments-display flow with REAL awaits so the
      // inline bubble actually renders before the screenshot (the old version
      // swallowed every step with .catch and captured blind). Wrapped best-effort
      // so a hiccup here never drops the downstream file-list capture.
      try {
        // Gate on composer hydration BEFORE touching it — the dedicated spec's key
        // step; this also absorbs the chat route's one-time cold compile.
        const composer = page.getByPlaceholder('메시지 입력...')
        await composer.waitFor({ state: 'visible', timeout: 90_000 })

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
        await page.getByText('membership-card.png').first().waitFor({ state: 'visible', timeout: 10_000 })
        await capture(page, WAVE, '14-attachment-composer.png')

        // Send and ASSERT the upload 201 so we know the attachment was actually
        // linked to the message (not silently dropped before the screenshot).
        const uploadResponse = page.waitForResponse(
          (r) => r.url().includes('/api/uploads') && r.request().method() === 'POST',
          { timeout: 30_000 },
        )
        await composer.fill('이 회원증 이미지 확인해줘')
        await page.getByRole('button', { name: /전송/ }).click()
        const upload = await uploadResponse
        if (upload.status() !== 201) throw new Error(`attachment upload failed: ${upload.status()}`)

        // Wait for the scripted reply, then the inline image by ALT TEXT — the
        // proven selector (looked up by message id from /files after the
        // message_id backfill), not a generic '[role=user] img'.
        await page
          .getByText('E2E scripted document model is ready.')
          .first()
          .waitFor({ state: 'visible', timeout: 60_000 })
        const userMsg = page.locator('[data-moldy-message-role="user"]').last()
        const inlineImg = userMsg.getByRole('img', { name: 'membership-card.png' })
        await inlineImg.waitFor({ state: 'visible', timeout: 30_000 })
        await page.waitForTimeout(800)
        await capture(page, WAVE, '15-attachment-bubble.png')

        // Click the inline thumbnail → full-screen lightbox (depends only on 15).
        await inlineImg.click()
        await page.waitForTimeout(1_200)
        await capture(page, WAVE, '17-image-lightbox.png')
        await page.keyboard.press('Escape').catch(() => {})
      } catch (error) {
        console.warn(`[capture-tour] attachment bubble/lightbox failed: ${String(error)}`)
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
      await page.goto(`/agents/${agent.id}/conversations/${cid}`, { waitUntil: 'commit', timeout: 120_000 }).catch(() => {})
      await page.getByPlaceholder('메시지 입력...').waitFor({ state: 'visible', timeout: 60_000 }).catch(() => {})
      await sendMessage(page, 'E2E_TOOL_GROUP')
      const traceRunId = await waitForActiveRun(request, cid).catch(() => '')
      await settleStream(page)
      // The /traces route redirects to the creation hub if the trace isn't ready
      // yet — poll the run to completion (and the trace to exist) before navigating.
      for (let i = 0; i < 30 && traceRunId; i += 1) {
        const run = await apiGetJson(
          request,
          `${API_BASE}/api/conversations/${cid}/runs/${traceRunId}`,
        ).catch(() => null)
        const st = isRecord(run) && typeof run.status === 'string' ? run.status : null
        if (st && !['queued', 'running', 'streaming'].includes(st)) break
        await page.waitForTimeout(1_000)
      }
      // Ensure the trace is materialized before navigating (mirrors the working
      // diagnostic, which fetched this before the /traces nav landed correctly).
      for (let i = 0; i < 15; i += 1) {
        const t = await apiGetJson(request, `${API_BASE}/api/conversations/${cid}/debug/traces`).catch(() => null)
        const traces = isRecord(t) && Array.isArray(t.traces) ? t.traces : []
        if (traces.length > 0) break
        await page.waitForTimeout(1_000)
      }
      await page.waitForTimeout(1_500)
      // 'commit' nav: under domcontentloaded the /traces route redirected to the
      // creation hub (a client-side race); commit lands on the trace page.
      await page
        .goto(`/agents/${agent.id}/conversations/${cid}/traces`, { waitUntil: 'commit', timeout: 120_000 })
        .catch(() => {})
      await page.getByText(/Trace 상세|트레이스/).first().waitFor({ state: 'visible', timeout: 30_000 }).catch(() => {})
      await settle(page, 1_800)
      // Capture the trace page as loaded first (proves it rendered).
      await capture(page, WAVE, '18-trace.png')
      // Span selection MUST be scoped to the trace grid — an unscoped getByText
      // matched the sidebar's "새 에이전트" and navigated to the creation hub.
      const grid = page.locator('.moldy-trace-grid')
      const selectSpan = async (pattern: RegExp): Promise<boolean> => {
        const node = grid.getByText(pattern).first()
        if ((await node.count()) > 0 && (await node.isVisible().catch(() => false))) {
          await node.click().catch(() => {})
          await page.waitForTimeout(900)
          return true
        }
        return false
      }
      ;(await selectSpan(/ChatModel|chat_model|call_model|LLM/i)) ||
        (await selectSpan(/current_datetime|resolve_relative|tool/i)) ||
        (await selectSpan(/agent|graph|run/i))
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
