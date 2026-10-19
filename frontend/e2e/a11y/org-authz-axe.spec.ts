import AxeBuilder from '@axe-core/playwright'

import { expect, loginApi, test } from '../fixtures'
import { log } from '../helpers/org-authz-api'
import { seed } from '../helpers/org-authz-seed'
import { runOrgAuthzTour, tourContext, type ScreenOptions } from '../helpers/org-authz-tour'

/**
 * Plan 1.3 "axe 심각·치명 0": every org-authz screen and dialog of the capture tour
 * (U-01..U-04, sharing, readiness, notifications, access request, invite page, admin
 * authz) is analyzed with axe. Serious/critical violations fail the test; minor and
 * moderate ones are attached and logged. Gated by E2E_ORG_AUTHZ=1 (scripted-full).
 */

const WCAG_TAGS = ['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa', 'wcag22aa']
const BLOCKING = new Set(['serious', 'critical'])

interface Finding {
  screen: string
  rule: string
  impact: string
  help: string
  nodes: Array<{ target: string; html: string; summary: string }>
}

test.describe('Org-authz screens — axe', () => {
  test.skip(process.env.E2E_ORG_AUTHZ !== '1', 'Set E2E_ORG_AUTHZ=1 to run the org-authz axe scan')
  test.use({ actionTimeout: 15_000, viewport: { width: 1440, height: 900 } })

  test('org-authz screens have no serious or critical axe violations', async ({
    page,
    request,
    playwright,
  }, testInfo) => {
    test.setTimeout(1_200_000)
    const findings: Finding[] = []
    const scanned: string[] = []

    async function scan(file: string, options: ScreenOptions = {}): Promise<void> {
      const screen = file.replace(/\.png$/, '')
      const results = await new AxeBuilder({ page: options.page ?? page })
        .withTags(WCAG_TAGS)
        .exclude('nextjs-portal')
        .analyze()
      scanned.push(screen)
      for (const violation of results.violations) {
        findings.push({
          screen,
          rule: violation.id,
          impact: violation.impact ?? 'unknown',
          help: violation.help,
          nodes: violation.nodes.slice(0, 5).map((node) => ({
            target: node.target.join(' '),
            html: node.html.slice(0, 240),
            summary: (node.failureSummary ?? '').slice(0, 300),
          })),
        })
      }
    }

    const csrf = await loginApi(request)
    const tour = tourContext({
      page,
      request,
      csrf,
      seed: await seed(playwright, request, csrf),
      screen: scan,
    })
    await runOrgAuthzTour(tour)

    const blocking = findings.filter((finding) => BLOCKING.has(finding.impact))
    const report = { scanned, failedSteps: tour.failed, findings }
    await testInfo.attach('org-authz-axe', {
      body: Buffer.from(JSON.stringify(report, null, 2)),
      contentType: 'application/json',
    })
    log(`scanned ${scanned.length}: ${scanned.join(', ')}`)
    log(`findings ${JSON.stringify(findings, null, 1)}`)

    expect(tour.failed, 'every org-authz screen is reachable').toEqual([])
    expect(blocking, 'serious/critical axe violations').toEqual([])
  })
})
