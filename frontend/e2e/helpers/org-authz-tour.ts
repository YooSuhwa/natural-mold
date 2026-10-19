import type { APIRequestContext, Locator, Page } from '@playwright/test'

import type { CsrfHeaders } from '../fixtures'
import { settle } from '../captures/_capture-helpers'
import { log, settleUi } from './org-authz-api'
import { installPlatformTenantStubs } from './org-authz-platform-stub'
import type { Seed } from './org-authz-seed'
import { companyAndOrgSteps } from './org-authz-tour-admin'
import { accessSteps } from './org-authz-tour-access'
import { sharingSteps } from './org-authz-tour-sharing'

/**
 * Every org-authz screen (U-01..U-04, E-04, G-04/G-05, H-01..H-08, invite page) in one
 * walk. Each reached screen is handed to `screen`: the capture tour screenshots it and
 * the axe scan analyzes it. A step that cannot reach its screen is recorded in `failed`.
 */

export interface ScreenOptions {
  fullPage?: boolean
  page?: Page
}

export interface TourContext {
  page: Page
  request: APIRequestContext
  csrf: CsrfHeaders
  seed: Seed
  screen: (file: string, options?: ScreenOptions) => Promise<void>
  failed: string[]
  visit: (url: string) => Promise<void>
  step: (name: string, fn: () => Promise<void>) => Promise<void>
  dialog: () => Locator
}

export type TourHooks = Pick<TourContext, 'page' | 'request' | 'csrf' | 'seed' | 'screen'> & {
  onStepFailed?: (name: string) => Promise<void>
}

export function tourContext(hooks: TourHooks): TourContext {
  const { page } = hooks
  const failed: string[] = []
  return {
    ...hooks,
    failed,
    async visit(url) {
      await page.goto(url, { waitUntil: 'domcontentloaded', timeout: 120_000 })
      await settle(page, 1_200)
    },
    async step(name, fn) {
      try {
        await fn()
      } catch (error) {
        failed.push(`${name}: ${String(error).split('\n')[0]}`)
        await hooks.onStepFailed?.(name).catch(() => {})
        log(`STEP FAILED ${name}: ${String(error).slice(0, 400)}`)
      }
      await page.keyboard.press('Escape').catch(() => {})
      await page.unrouteAll({ behavior: 'ignoreErrors' }).catch(() => {})
    },
    dialog: () => page.locator('[role="dialog"], [role="alertdialog"]').last(),
  }
}

async function platformSteps(t: TourContext): Promise<void> {
  const { page, seed: s, dialog } = t
  // Real platform tenants exist only with DEPLOYMENT_MODE=multi_tenant; otherwise stub them.
  const stubbed = s.platformTenantIds.length === 0
  const tenantIds = stubbed ? ['stub-tenant-1'] : s.platformTenantIds
  const stub = async () => {
    if (stubbed) await installPlatformTenantStubs(page)
  }
  await t.step('u01-list', async () => {
    await stub()
    await t.visit('/settings/admin/tenants')
    await page.getByText('한빛물산').first().waitFor({ timeout: 30_000 })
    await t.screen('01-u01-tenant-list.png', { fullPage: true })
    await page.getByRole('row').filter({ hasText: '한빛물산' }).getByRole('button').last().click()
    await page.getByRole('menuitem', { name: '정지' }).click()
    await dialog().waitFor()
    await settleUi(page)
    await t.screen('02-u01-tenant-suspend-confirm.png')
  })
  await t.step('u01-risky-delete', async () => {
    await stub()
    await t.visit('/settings/admin/tenants')
    await page.getByRole('row').filter({ hasText: '세종에듀' }).getByRole('button').last().click()
    await page.getByRole('menuitem', { name: '삭제 예약' }).click()
    await dialog().waitFor()
    await settleUi(page)
    await t.screen('03-u01-tenant-schedule-deletion-confirm.png')
  })
  await t.step('u01-detail', async () => {
    await stub()
    await t.visit(`/settings/admin/tenants/${tenantIds[0]}`)
    await page.getByText('한빛물산').first().waitFor({ timeout: 30_000 })
    await settleUi(page)
    await t.screen('04-u01-tenant-detail.png', { fullPage: true })
  })
}

export async function runOrgAuthzTour(t: TourContext): Promise<void> {
  await platformSteps(t)
  await companyAndOrgSteps(t)
  await sharingSteps(t)
  await accessSteps(t)
}
