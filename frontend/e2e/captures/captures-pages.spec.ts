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
 * Wave 2 — page/route capture tour. Navigates every authenticated surface and
 * screenshots it. Each page is wrapped so one bad surface never aborts the rest.
 * Gated by E2E_CAPTURE_TOUR=1. Needs E2E_SEED_USER_ENABLED=true for operator pages.
 */

const WAVE = 'wave2-pages'

const AUTHED_PAGES: ReadonlyArray<readonly [string, string]> = [
  ['/', '01-dashboard.png'],
  ['/agents/new', '02-agent-new-hub.png'],
  ['/agents/new/manual', '03-agent-new-manual.png'],
  ['/agents/new/template', '04-agent-new-template.png'],
  ['/skills', '10-skills.png'],
  ['/tools', '11-tools.png'],
  ['/mcp-servers', '12-mcp-servers.png'],
  ['/marketplace', '13-marketplace.png'],
  ['/artifacts', '14-artifacts.png'],
  ['/usage', '15-usage.png'],
  ['/settings', '20-settings-profile.png'],
  ['/settings/appearance', '21-settings-appearance.png'],
  ['/settings/credentials', '22-settings-credentials.png'],
  ['/settings/agent-api', '23-settings-agent-api.png'],
  ['/settings/audit', '24-settings-audit.png'],
  ['/settings/security', '25-settings-security.png'],
  ['/settings/memory', '26-settings-memory.png'],
  ['/settings/artifacts', '27-settings-artifacts.png'],
  ['/settings/models', '28-settings-models.png'],
  ['/settings/schedules', '29-settings-schedules.png'],
]

const OPERATOR_PAGES: ReadonlyArray<readonly [string, string]> = [
  ['/settings/system-llm', '40-system-llm.png'],
  ['/settings/system-credentials', '41-system-credentials.png'],
  ['/settings/admin-audit', '42-admin-audit.png'],
  ['/settings/marketplace-admin', '43-marketplace-admin.png'],
]

async function tourCapture(page: Page, url: string, file: string): Promise<void> {
  // Best-effort with a retry: Next dev compiles each route on first visit, so a
  // cold heavy route (dashboard, builder) can exceed the first goto budget.
  for (let attempt = 1; attempt <= 2; attempt += 1) {
    try {
      await page.goto(url, { waitUntil: 'domcontentloaded', timeout: 90_000 })
      await settle(page)
      await capture(page, WAVE, file)
      return
    } catch (error) {
      console.warn(`[capture-tour] ${url} → ${file} attempt ${attempt} failed: ${String(error)}`)
      if (attempt === 2) return
      await page.waitForTimeout(2_000)
    }
  }
}

test.describe('Wave 2 — page tour captures', () => {
  test.skip(process.env.E2E_CAPTURE_TOUR !== '1', 'Set E2E_CAPTURE_TOUR=1 to run the capture tour')

  test.beforeEach(async ({ page }) => {
    await page.setViewportSize(DESKTOP_VIEWPORT)
  })

  test('captures all authenticated pages with realistic seed data', async ({ page, request }) => {
    test.setTimeout(300_000)
    const csrfHeaders = await loginApi(request)
    const agentIds = await seedRealisticAgents(request, csrfHeaders)
    try {
      for (const [url, file] of AUTHED_PAGES) {
        await tourCapture(page, url, file)
      }
    } finally {
      await deleteAgents(request, csrfHeaders, agentIds)
    }
  })

  test('captures operator (super_user) pages', async ({ page }) => {
    test.setTimeout(180_000)
    for (const [url, file] of OPERATOR_PAGES) {
      await tourCapture(page, url, file)
    }
  })
})

test.describe('Wave 2 — auth pages (logged out)', () => {
  test.skip(process.env.E2E_CAPTURE_TOUR !== '1', 'Set E2E_CAPTURE_TOUR=1 to run the capture tour')

  test('captures login and register on a clean context', async ({ browser }) => {
    const context = await browser.newContext({ viewport: DESKTOP_VIEWPORT })
    const page = await context.newPage()
    try {
      await tourCapture(page, '/login', '50-login.png')
      await tourCapture(page, '/register', '51-register.png')
    } finally {
      await context.close()
    }
  })
})
