import { settle } from '../captures/_capture-helpers'
import { settleUi } from './org-authz-api'
import type { TourContext } from './org-authz-tour'

/** U-02 company settings, H-08 SSO, U-03 organization settings, U-04 member removal. */
export async function companyAndOrgSteps(t: TourContext): Promise<void> {
  const { page, dialog } = t
  await t.step('u02-overview', async () => {
    await t.visit('/settings/company')
    await page.getByText('마케팅본부').first().waitFor({ timeout: 30_000 })
    await t.screen('05-u02-company-overview.png', { fullPage: true })
    await page.getByRole('button', { name: '마케팅본부 보관' }).click()
    await dialog().waitFor()
    await settleUi(page)
    await t.screen('06-u04-org-archive-dialog.png')
  })
  await t.step('u02-policies', async () => {
    await t.visit('/settings/company/policies')
    await t.screen('07-u02-company-policies.png', { fullPage: true })
  })
  await t.step('u02-members', async () => {
    await t.visit('/settings/company/members')
    await page.getByText('박서준').first().waitFor({ timeout: 30_000 })
    await t.screen('08-u02-company-members.png', { fullPage: true })
    await page.getByRole('button', { name: '회사에 초대' }).first().click()
    await dialog().waitFor()
    await settleUi(page)
    await t.screen('09-u02-company-invite-dialog.png')
  })
  const companyTabs: ReadonlyArray<readonly [string, string]> = [
    ['모델', '10-u02-company-models.png'],
    ['공용 자격증명', '11-u02-company-shared-credentials.png'],
    ['서비스 LLM', '12-u02-company-service-llm.png'],
    ['회사 카탈로그', '13-u02-company-catalog.png'],
  ]
  await t.step('u02-models', async () => {
    await t.visit('/settings/company/models')
    for (const [tab, file] of companyTabs) {
      await page.getByRole('tab', { name: tab, exact: true }).click()
      await settle(page, 800)
      await t.screen(file, { fullPage: true })
    }
  })
  await t.step('h08-sso', async () => {
    await t.visit('/settings/company/sso')
    await page.getByText('nuritech.example').first().waitFor({ timeout: 20_000 })
    await t.screen('14-h08-company-sso.png', { fullPage: true })
  })
  await organizationSteps(t)
  await removalSteps(t)
}

async function organizationSteps(t: TourContext): Promise<void> {
  const { page, dialog } = t
  await t.step('u03-settings', async () => {
    await t.visit('/settings/organization')
    await page.getByText('사용 한도').first().waitFor({ timeout: 20_000 })
    await t.screen('15-u03-org-settings.png', { fullPage: true })
  })
  await t.step('u03-catalog', async () => {
    await t.visit('/settings/organization/catalog')
    await settle(page, 800)
    await t.screen('16-u03-org-catalog.png', { fullPage: true })
    await page.getByRole('switch', { checked: true }).first().click()
    await dialog().waitFor()
    await settleUi(page)
    await t.screen('17-u03-org-catalog-disable-confirm.png')
  })
  await t.step('u03-members', async () => {
    await t.visit('/settings/organization/members')
    await page.getByText('박서준').first().waitFor({ timeout: 20_000 })
    await t.screen('18-u03-org-members.png', { fullPage: true })
    await page.getByRole('button', { name: '구성원 초대' }).first().click()
    await dialog().waitFor()
    await dialog()
      .getByRole('textbox')
      .first()
      .fill('minji.kim@nuritech.example, jihoon.oh@nuritech.example')
    await settleUi(page)
    await t.screen('20-u03-org-invite-dialog.png')
    await dialog()
      .getByRole('button', { name: /초대 보내기/ })
      .click()
    await dialog().getByText('초대 링크를 만들었어요').first().waitFor({ timeout: 15_000 })
    await settleUi(page)
    await t.screen('21-u03-org-invite-links.png')
  })
  await t.step('h06-member-limit', async () => {
    await t.visit('/settings/organization/members')
    const row = page.getByRole('row').filter({ hasText: '이하은' })
    await row.getByRole('button', { name: '월 한도' }).click()
    await dialog().waitFor()
    await settleUi(page)
    await t.screen('22-h06-member-monthly-limit-dialog.png')
  })
  await t.step('u03-roles', async () => {
    await t.visit('/settings/organization/roles')
    await settle(page, 800)
    await t.screen('23-u03-org-roles.png', { fullPage: true })
  })
  await t.step('u03-groups', async () => {
    await t.visit('/settings/organization/groups')
    await page.getByText('플랫폼팀').first().waitFor({ timeout: 20_000 })
    await page
      .getByText('플랫폼팀', { exact: true })
      .locator('xpath=ancestor::*[.//button][1]')
      .getByRole('button', { name: '구성원', exact: true })
      .click()
      .catch(() => {})
    await settle(page, 600)
    await t.screen('24-u03-org-groups.png', { fullPage: true })
  })
  await t.step('u03-requests-real', async () => {
    await t.visit('/settings/organization/requests')
    await t.screen('25-u03-org-requests-empty.png', { fullPage: true })
  })
}

async function removalSteps(t: TourContext): Promise<void> {
  const { page, dialog } = t
  await t.step('u04-removal', async () => {
    await t.visit('/settings/organization/members')
    const row = page.getByRole('row').filter({ hasText: '박서준' })
    await row.getByRole('button', { name: '제거' }).click()
    await dialog().waitFor()
    await dialog().getByText('주간 영업 리포트 봇').first().waitFor({ timeout: 20_000 })
    await settleUi(page)
    await t.screen('26-u04-removal-step1-resources.png')
    const picker = dialog().getByRole('combobox', { name: '모든 이전 항목의 새 소유자' })
    await picker.fill('김지')
    await page.getByRole('option').first().click({ timeout: 15_000 })
    await settleUi(page)
    await t.screen('27-u04-removal-step1-owner-selected.png')
    await dialog().getByRole('button', { name: '다음' }).click()
    await settleUi(page)
    await t.screen('28-u04-removal-step2-effects.png')
    await dialog().getByRole('button', { name: '다음' }).click()
    await dialog().getByRole('textbox').last().fill('부서 이동 (영업본부 → 마케팅본부)')
    await settleUi(page)
    await t.screen('29-u04-removal-step3-confirm.png')
    await dialog().getByRole('button', { name: '이전', exact: true }).click()
    await dialog().getByRole('button', { name: '이전', exact: true }).click()
    await settleUi(page)
    // "Transfer now" is offered only when the caller can govern the resource;
    // legacy mode hides it by design, so shot 30 exists only in fga mode.
    const transferNow = dialog().getByRole('button', { name: /지금 바로 이전/ })
    if ((await transferNow.count()) > 0) {
      await transferNow.first().click()
      await settleUi(page)
      await t.screen('30-u04-transfer-ownership-dialog.png')
    }
  })
}
