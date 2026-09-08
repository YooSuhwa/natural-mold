import AxeBuilder from '@axe-core/playwright'

import { expect, test } from './fixtures'
import { ONBOARDING_DISMISSED_FLAG, SUPER_USER_WELCOMED_FLAG } from '../src/lib/auth/session-flags'

const REPRESENTATIVE_ROUTES = ['/', '/marketplace', '/settings'] as const

type RuntimePerformance = {
  cumulativeLayoutShift: number
  domContentLoadedMs: number
  longestTaskMs: number
}

test.beforeEach(async ({ page }) => {
  await page.addInitScript(
    ({ onboardingDismissedFlag, superUserWelcomedFlag }) => {
      window.sessionStorage.setItem(onboardingDismissedFlag, '1')
      window.sessionStorage.setItem(superUserWelcomedFlag, '1')

      const metrics = {
        cumulativeLayoutShift: 0,
        longestTaskMs: 0,
      }
      Object.defineProperty(window, '__moldyPerformanceMetrics', {
        configurable: true,
        value: metrics,
      })

      new PerformanceObserver((entries) => {
        for (const entry of entries.getEntries()) {
          const shift = entry as PerformanceEntry & { hadRecentInput?: boolean; value?: number }
          if (!shift.hadRecentInput) metrics.cumulativeLayoutShift += shift.value ?? 0
        }
      }).observe({ type: 'layout-shift', buffered: true })

      new PerformanceObserver((entries) => {
        for (const entry of entries.getEntries()) {
          metrics.longestTaskMs = Math.max(metrics.longestTaskMs, entry.duration)
        }
      }).observe({ type: 'longtask', buffered: true })
    },
    {
      onboardingDismissedFlag: ONBOARDING_DISMISSED_FLAG,
      superUserWelcomedFlag: SUPER_USER_WELCOMED_FLAG,
    },
  )
})

test('keyboard users can skip repeated navigation and return focus to the main content', async ({
  page,
  errors,
}) => {
  await page.goto('/')
  await expect(page.getByRole('heading', { name: /E2E User님/ })).toBeVisible()

  await page.keyboard.press('Tab')
  const skipLink = page.getByRole('link', { name: '본문으로 건너뛰기' })
  await expect(skipLink).toBeFocused()
  await expect(skipLink).toBeVisible()

  await page.keyboard.press('Enter')
  await expect(page.getByRole('main')).toBeFocused()

  expect(errors.console).toEqual([])
  expect(errors.network).toEqual([])
})

for (const route of REPRESENTATIVE_ROUTES) {
  test(`${route} has no serious accessibility violations`, async ({ page, errors }, testInfo) => {
    await page.goto(route)
    await expect(page.getByRole('main')).toBeVisible()

    const results = await new AxeBuilder({ page })
      .withTags(['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa', 'wcag22aa'])
      .analyze()
    const blockingViolations = results.violations.filter(
      ({ impact }) => impact === 'serious' || impact === 'critical',
    )

    await testInfo.attach('axe-results', {
      body: Buffer.from(JSON.stringify(results, null, 2)),
      contentType: 'application/json',
    })
    expect(blockingViolations).toEqual([])
    expect(errors.console).toEqual([])
    expect(errors.network).toEqual([])
  })
}

test('dashboard stays within interaction and layout stability budgets', async ({
  page,
  errors,
}) => {
  await page.goto('/')
  await expect(page.getByRole('heading', { name: /E2E User님/ })).toBeVisible()
  await page.evaluate(
    () =>
      new Promise<void>((resolve) =>
        requestAnimationFrame(() => requestAnimationFrame(() => resolve())),
      ),
  )

  const metrics = await page.evaluate<RuntimePerformance>(() => {
    const navigation = performance.getEntriesByType('navigation')[0] as
      | PerformanceNavigationTiming
      | undefined
    const runtime = (
      window as Window & {
        __moldyPerformanceMetrics?: {
          cumulativeLayoutShift: number
          longestTaskMs: number
        }
      }
    ).__moldyPerformanceMetrics
    return {
      cumulativeLayoutShift: runtime?.cumulativeLayoutShift ?? 0,
      domContentLoadedMs: navigation?.domContentLoadedEventEnd ?? Number.POSITIVE_INFINITY,
      longestTaskMs: runtime?.longestTaskMs ?? 0,
    }
  })

  expect(metrics.cumulativeLayoutShift).toBeLessThanOrEqual(0.1)
  expect(metrics.domContentLoadedMs).toBeLessThan(5_000)
  expect(metrics.longestTaskMs).toBeLessThan(500)
  expect(errors.console).toEqual([])
  expect(errors.network).toEqual([])
})
