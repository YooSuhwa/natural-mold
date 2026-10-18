import fs from 'node:fs/promises'
import path from 'node:path'

import { loginApi, test } from '../fixtures'
import { log } from '../helpers/org-authz-api'
import { seed } from '../helpers/org-authz-seed'
import { runOrgAuthzTour, tourContext, type ScreenOptions } from '../helpers/org-authz-tour'

/**
 * Org-authz capture tour (U-01..U-04, E-04, G-04/G-05, H-01..H-08). Gated by
 * E2E_CAPTURE_TOUR=1. Needs E2E_SEED_USER_ENABLED=true (seeded super user = company + org
 * admin of the E2E scope). DEPLOYMENT_MODE=multi_tenant gives real platform tenants;
 * without it the U-01 screens are route-played.
 *
 * Real data is seeded over the API. States that only exist in AUTHZ_MODE=enforce
 * (403 permission_required, runner views, readiness/auth cards, access-request inbox) are
 * played over `page.route` the same way the opt-in org-authz specs do. The walk itself is
 * shared with the axe scan (e2e/a11y/org-authz-axe.spec.ts).
 */

const OUT_DIR = path.join('..', 'output', 'captures', 'org-authz')
const VIEWPORT = { width: 1440, height: 900 } as const

test.describe('Org-authz screens — capture tour', () => {
  test.skip(process.env.E2E_CAPTURE_TOUR !== '1', 'Set E2E_CAPTURE_TOUR=1 to run the capture tour')
  test.use({ actionTimeout: 15_000 })

  test('captures every org-authz screen', async ({ page, request, playwright }) => {
    test.setTimeout(1_500_000)
    await fs.mkdir(OUT_DIR, { recursive: true })
    await page.setViewportSize(VIEWPORT)
    const done: string[] = []

    async function shot(file: string, options: ScreenOptions = {}): Promise<void> {
      const target = options.page ?? page
      if (!options.fullPage) {
        await target.screenshot({ path: path.join(OUT_DIR, file) })
        done.push(file)
        return
      }
      // The app shell scrolls an inner container, so `fullPage` alone stays at the
      // viewport: grow the viewport by the inner overflow, capture, then restore.
      const overflow = await target.evaluate(() => {
        let extra = 0
        for (const el of Array.from(document.querySelectorAll<HTMLElement>('*'))) {
          const style = getComputedStyle(el)
          if (!/(auto|scroll)/.test(style.overflowY)) continue
          if (el.clientHeight < 300) continue
          extra = Math.max(extra, el.scrollHeight - el.clientHeight)
        }
        return extra
      })
      const height = Math.min(VIEWPORT.height + overflow, 4_000)
      if (height > VIEWPORT.height) {
        await target.setViewportSize({ width: VIEWPORT.width, height })
        await target.waitForTimeout(600)
      }
      await target.screenshot({ path: path.join(OUT_DIR, file), fullPage: true })
      if (height > VIEWPORT.height) await target.setViewportSize(VIEWPORT)
      done.push(file)
    }

    const csrf = await loginApi(request)
    const tour = tourContext({
      page,
      request,
      csrf,
      seed: await seed(playwright, request, csrf),
      screen: shot,
      onStepFailed: (name) =>
        page.screenshot({ path: path.join(OUT_DIR, `zz-debug-${name}.png`) }).then(() => {}),
    })
    await runOrgAuthzTour(tour)

    log(`captured ${done.length}: ${done.join(', ')}`)
    log(`failed ${tour.failed.length}: ${JSON.stringify(tour.failed, null, 1)}`)
  })
})
