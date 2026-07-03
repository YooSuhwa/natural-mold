import { expect, test } from '../fixtures'
import {
  capture,
  captureLocator,
  DESKTOP_VIEWPORT,
  settle,
  warmUpChatRoute,
} from './_capture-helpers'

/**
 * Wave — OpenWiki marketplace skill + template one-click agent creation.
 * Walks the real delivered flow: marketplace listing → item detail →
 * template gallery (skill chip) → one-click create (auto-installs and
 * attaches the openwiki skill) → agent settings + skills library.
 * Gated by E2E_CAPTURE_TOUR=1.
 */

const WAVE = 'wave-openwiki'
const TEMPLATE_NAME = 'OpenWiki 문서화 에이전트'

test.describe('OpenWiki skill + template captures', () => {
  test.skip(process.env.E2E_CAPTURE_TOUR !== '1', 'Set E2E_CAPTURE_TOUR=1 to run the capture tour')

  test.beforeAll(async ({ browser }) => {
    test.setTimeout(300_000)
    await warmUpChatRoute(browser)
  })

  test('captures marketplace, template gallery, one-click creation, attached skill', async ({
    page,
  }) => {
    test.setTimeout(600_000)
    await page.setViewportSize(DESKTOP_VIEWPORT)

    // 1) Marketplace list — the seeded OpenWiki system skill card.
    await page.goto('/marketplace', { waitUntil: 'domcontentloaded', timeout: 280_000 })
    const marketplaceCard = page
      .locator('article')
      .filter({ hasText: 'OpenWiki documentation site' })
      .first()
    await expect(marketplaceCard).toBeVisible({ timeout: 120_000 })
    await settle(page)
    await capture(page, WAVE, '00-marketplace-list-openwiki.png')

    // 2) Marketplace item detail — cards navigate via the 상세 보기 link.
    await marketplaceCard.getByRole('link', { name: '상세 보기' }).click()
    await page.waitForURL(/\/marketplace\/[0-9a-f-]{36}/, { timeout: 60_000 })
    await expect(page.getByText('OpenWiki').first()).toBeVisible({ timeout: 60_000 })
    await settle(page)
    await capture(page, WAVE, '01-marketplace-openwiki-detail.png')

    // 3) Template gallery — OpenWiki template card with the skill chip.
    await page.goto('/agents/new/template', { waitUntil: 'domcontentloaded', timeout: 280_000 })
    const templateCard = page.getByRole('button', { name: new RegExp(TEMPLATE_NAME) }).first()
    await expect(templateCard).toBeVisible({ timeout: 120_000 })
    await settle(page)
    await capture(page, WAVE, '02-template-gallery.png')
    await captureLocator(templateCard, WAVE, '03-template-card-skill-chip.png')

    // 4) One-click create → /agents/<id> redirects to the fresh chat view.
    await templateCard.click()
    await page.waitForURL(/\/agents\/[0-9a-f-]{36}\/conversations\//, { timeout: 280_000 })
    await page
      .locator('textarea[data-moldy-composer-input="true"]')
      .last()
      .waitFor({ state: 'visible', timeout: 120_000 })
    await expect(page.getByText(TEMPLATE_NAME).first()).toBeVisible({ timeout: 60_000 })
    await page.waitForTimeout(1_500)
    await settle(page)
    await capture(page, WAVE, '04-agent-created-chat.png')

    const agentId = new URL(page.url()).pathname.split('/')[2] ?? ''

    // 5) Agent settings — the auto-attached openwiki skill.
    await page.goto(`/agents/${agentId}/settings`, {
      waitUntil: 'domcontentloaded',
      timeout: 280_000,
    })
    await expect(page.getByText('openwiki').first()).toBeVisible({ timeout: 120_000 })
    await settle(page)
    await capture(page, WAVE, '05-agent-settings-attached-skill.png')

    // 6) Skills library — the installed user copy of the skill.
    await page.goto('/skills', { waitUntil: 'domcontentloaded', timeout: 280_000 })
    await expect(page.getByText('openwiki').first()).toBeVisible({ timeout: 120_000 })
    await settle(page)
    await capture(page, WAVE, '06-skills-library-installed.png')
  })
})
