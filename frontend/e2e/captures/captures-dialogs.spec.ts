import type { Page } from '@playwright/test'
import { API_BASE, apiPostJson, loginApi, test } from '../fixtures'
import {
  capture,
  createConfiguredAgent,
  deleteAgents,
  DESKTOP_VIEWPORT,
  seedRealisticAgents,
  settle,
} from './_capture-helpers'

/**
 * Wave 3 — dialog/modal captures. Navigates to a resource page, clicks its
 * primary CTA (trying several Korean label candidates), waits for the dialog,
 * and screenshots it. Best-effort per dialog so a moved/renamed button never
 * drops the rest. Gated by E2E_CAPTURE_TOUR=1.
 */

const WAVE = 'wave3-dialogs'

type DialogCase = {
  readonly url: string
  readonly file: string
  /** Button label candidates (first visible one is clicked to open the dialog). */
  readonly open: ReadonlyArray<string | RegExp>
}

// Dialogs that open from a labeled CTA on a list page (generic flow). The three
// that DON'T fit this shape — tool (opens from a catalog card), schedule (created
// per-agent, not on /settings/schedules), api-key (button disabled until a
// deployment exists) — are dedicated tests below.
const DIALOGS: ReadonlyArray<DialogCase> = [
  { url: '/settings/credentials', file: '01-credential-create.png', open: [/자격증명 추가|추가|새 자격증명|등록/] },
  { url: '/skills', file: '03-skill-create.png', open: [/새 스킬|스킬 추가|스킬 만들기|첫 스킬|추가|만들기|업로드/] },
  { url: '/mcp-servers', file: '04-mcp-add.png', open: [/새 서버|서버 추가|MCP 추가|추가|가져오기|연결|등록/] },
  { url: '/settings/models', file: '06-model-add.png', open: [/새 모델|모델 추가|추가|등록/] },
]

async function clickFirstVisible(page: Page, candidates: ReadonlyArray<string | RegExp>): Promise<boolean> {
  for (const name of candidates) {
    for (const role of ['button', 'link'] as const) {
      const el = page.getByRole(role, { name }).first()
      if ((await el.count()) > 0 && (await el.isVisible().catch(() => false))) {
        await el.click().catch(() => {})
        return true
      }
    }
  }
  return false
}

async function waitDialog(page: Page): Promise<void> {
  await page
    .locator('[role="dialog"], [role="alertdialog"]')
    .first()
    .waitFor({ state: 'visible', timeout: 12_000 })
    .catch(() => {})
  await page.waitForTimeout(800)
}

async function clickTab(page: Page, name: RegExp): Promise<void> {
  for (const role of ['tab', 'button', 'link'] as const) {
    const el = page.getByRole(role, { name }).first()
    if ((await el.count()) > 0 && (await el.isVisible().catch(() => false))) {
      await el.click().catch(() => {})
      await page.waitForTimeout(600)
      return
    }
  }
}

async function openDialogAndCapture(page: Page, item: DialogCase): Promise<void> {
  try {
    await page.goto(item.url, { waitUntil: 'domcontentloaded', timeout: 120_000 })
    await settle(page)
    const opened = await clickFirstVisible(page, item.open)
    if (!opened) {
      console.warn(`[capture-tour] dialog ${item.file}: no CTA matched on ${item.url}`)
      return
    }
    await waitDialog(page)
    await capture(page, WAVE, item.file)
  } catch (error) {
    console.warn(`[capture-tour] dialog ${item.file} failed: ${String(error)}`)
  }
}

test.describe('Wave 3 — dialog captures', () => {
  test.skip(process.env.E2E_CAPTURE_TOUR !== '1', 'Set E2E_CAPTURE_TOUR=1 to run the capture tour')

  test.beforeEach(async ({ page }) => {
    await page.setViewportSize(DESKTOP_VIEWPORT)
  })

  test('captures resource-creation dialogs', async ({ page, request }) => {
    test.setTimeout(300_000)
    const csrfHeaders = await loginApi(request)
    const agentIds = await seedRealisticAgents(request, csrfHeaders)
    try {
      for (const item of DIALOGS) {
        await openDialogAndCapture(page, item)
      }
    } finally {
      await deleteAgents(request, csrfHeaders, agentIds)
    }
  })

  test('captures the agent delete confirmation dialog', async ({ page, request }) => {
    test.setTimeout(180_000)
    const csrfHeaders = await loginApi(request)
    const agentIds = await seedRealisticAgents(request, csrfHeaders)
    try {
      await page.goto(`/agents/${agentIds[0]}/settings`, {
        waitUntil: 'domcontentloaded',
        timeout: 120_000,
      })
      await settle(page)
      await clickFirstVisible(page, [/삭제/])
      await page
        .locator('[role="dialog"], [role="alertdialog"]')
        .first()
        .waitFor({ state: 'visible', timeout: 12_000 })
        .catch(() => {})
      await page.waitForTimeout(600)
      await capture(page, WAVE, '08-agent-delete-confirm.png')
    } finally {
      await deleteAgents(request, csrfHeaders, agentIds)
    }
  })

  // Tool create opens from a catalog card (the /tools 'all' tab), not a CTA button,
  // so the generic label-matching flow can't reach it.
  test('captures the tool create dialog (from catalog card)', async ({ page, request }) => {
    test.setTimeout(120_000)
    await loginApi(request)
    try {
      await page.goto('/tools', { waitUntil: 'domcontentloaded', timeout: 120_000 })
      await settle(page)
      const card = page.getByTestId('tool-catalog-card').first()
      await card.waitFor({ state: 'visible', timeout: 30_000 })
      await card.click()
      await waitDialog(page)
      await capture(page, WAVE, '02-tool-create.png')
    } catch (error) {
      console.warn(`[capture-tour] dialog 02-tool-create failed: ${String(error)}`)
    }
  })

  // Schedules are created PER-AGENT (settings → 스케줄 tab → 추가), not on the
  // /settings/schedules list page (which only manages existing triggers).
  test('captures the schedule create dialog (per-agent)', async ({ page, request }) => {
    test.setTimeout(180_000)
    const csrfHeaders = await loginApi(request)
    const { agentId, childId } = await createConfiguredAgent(request, csrfHeaders)
    try {
      await page.goto(`/agents/${agentId}/settings`, {
        waitUntil: 'domcontentloaded',
        timeout: 120_000,
      })
      await settle(page)
      await clickTab(page, /스케줄/)
      await page.getByTestId('trigger-add-button').click()
      await waitDialog(page)
      await capture(page, WAVE, '05-schedule-create.png')
    } catch (error) {
      console.warn(`[capture-tour] dialog 05-schedule-create failed: ${String(error)}`)
    } finally {
      await deleteAgents(request, csrfHeaders, [agentId, ...(childId ? [childId] : [])])
    }
  })

  // The API-key CTA is disabled until a deployment exists, so seed one first
  // (requires a fixed-identity agent — createConfiguredAgent provides that).
  test('captures the api-key create dialog', async ({ page, request }) => {
    test.setTimeout(180_000)
    const csrfHeaders = await loginApi(request)
    const { agentId, childId } = await createConfiguredAgent(request, csrfHeaders)
    try {
      await apiPostJson(request, `${API_BASE}/api/agent-api/deployments`, csrfHeaders, {
        agent_id: agentId,
      })
      await page.goto('/settings/agent-api', { waitUntil: 'domcontentloaded', timeout: 120_000 })
      await settle(page)
      const createKey = page.getByTestId('api-key-create-button')
      await createKey.waitFor({ state: 'visible', timeout: 30_000 })
      await createKey.click()
      await waitDialog(page)
      await capture(page, WAVE, '07-api-key-create.png')
    } catch (error) {
      console.warn(`[capture-tour] dialog 07-api-key-create failed: ${String(error)}`)
    } finally {
      await deleteAgents(request, csrfHeaders, [agentId, ...(childId ? [childId] : [])])
    }
  })
})
