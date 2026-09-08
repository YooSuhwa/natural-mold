import { expect, test } from '../fixtures'
import { capture, captureViewport } from './_capture-helpers'
import {
  ONBOARDING_DISMISSED_FLAG,
  SUPER_USER_WELCOMED_FLAG,
} from '../../src/lib/auth/session-flags'

const WAVE = 'accessibility-validation'
const VIEWPORTS = [
  { name: 'mobile', width: 375, height: 812 },
  { name: 'tablet', width: 768, height: 1024 },
  { name: 'desktop', width: 1280, height: 720 },
] as const
const ROUTES = [
  { name: 'dashboard', path: '/' },
  { name: 'marketplace', path: '/marketplace' },
  { name: 'settings', path: '/settings' },
] as const

test('captures accessibility validation surfaces and focused skip link', async ({ page }) => {
  test.skip(process.env.E2E_CAPTURE_TOUR !== '1', 'Set E2E_CAPTURE_TOUR=1 to run the capture tour')
  test.setTimeout(120_000)

  await page.addInitScript(
    ({ onboardingDismissedFlag, superUserWelcomedFlag }) => {
      window.sessionStorage.setItem(onboardingDismissedFlag, '1')
      window.sessionStorage.setItem(superUserWelcomedFlag, '1')
    },
    {
      onboardingDismissedFlag: ONBOARDING_DISMISSED_FLAG,
      superUserWelcomedFlag: SUPER_USER_WELCOMED_FLAG,
    },
  )

  for (const viewport of VIEWPORTS) {
    await page.setViewportSize(viewport)
    for (const route of ROUTES) {
      await page.goto(route.path)
      await expect(page.getByRole('main')).toBeVisible()
      await page.evaluate(
        () =>
          new Promise<void>((resolve) =>
            requestAnimationFrame(() => requestAnimationFrame(() => resolve())),
          ),
      )
      await capture(page, WAVE, `${viewport.name}-${route.name}.png`)

      if (route.path === '/') {
        await page.keyboard.press('Tab')
        await expect(page.getByRole('link', { name: '본문으로 건너뛰기' })).toBeVisible()
        await captureViewport(page, WAVE, `${viewport.name}-dashboard-skip-focused.png`)
      }
    }
  }
})
