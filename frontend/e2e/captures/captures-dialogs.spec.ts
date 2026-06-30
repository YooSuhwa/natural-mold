import type { Page } from '@playwright/test'
import { loginApi, test } from '../fixtures'
import {
  capture,
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

const DIALOGS: ReadonlyArray<DialogCase> = [
  { url: '/settings/credentials', file: '01-credential-create.png', open: [/자격증명 추가|추가|새 자격증명|등록/] },
  { url: '/tools', file: '02-tool-create.png', open: [/새 도구|도구 추가|도구 만들기|도구 등록|도구 연결|만들기/] },
  { url: '/skills', file: '03-skill-create.png', open: [/새 스킬|스킬 추가|스킬 만들기|첫 스킬|추가|만들기|업로드/] },
  { url: '/mcp-servers', file: '04-mcp-add.png', open: [/새 서버|서버 추가|MCP 추가|추가|가져오기|연결|등록/] },
  { url: '/settings/schedules', file: '05-schedule-create.png', open: [/새 스케줄|새 트리거|스케줄 추가|트리거 추가|예약|추가|만들기/] },
  { url: '/settings/models', file: '06-model-add.png', open: [/새 모델|모델 추가|추가|등록/] },
  { url: '/settings/agent-api', file: '07-api-key-create.png', open: [/새 키|새 API|API 키 발급|키 발급|키 생성|발급|추가|생성/] },
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

async function openDialogAndCapture(page: Page, item: DialogCase): Promise<void> {
  try {
    await page.goto(item.url, { waitUntil: 'domcontentloaded', timeout: 120_000 })
    await settle(page)
    const opened = await clickFirstVisible(page, item.open)
    if (!opened) {
      console.warn(`[capture-tour] dialog ${item.file}: no CTA matched on ${item.url}`)
      return
    }
    await page
      .locator('[role="dialog"], [role="alertdialog"]')
      .first()
      .waitFor({ state: 'visible', timeout: 12_000 })
      .catch(() => {})
    await page.waitForTimeout(800)
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
})
