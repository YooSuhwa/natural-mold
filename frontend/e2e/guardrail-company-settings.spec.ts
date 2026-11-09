import fs from 'node:fs/promises'
import path from 'node:path'

import type { Page } from '@playwright/test'

import {
  API_BASE,
  apiGetJson,
  apiJson,
  apiPostJson,
  isRecord,
  loginApi,
  test,
  expect,
} from './fixtures'
import {
  restoreCompanyPolicy,
  saveCompanyPolicy,
  type SavedCompanyPolicy,
} from './helpers/company-guardrails'
import { isSeededScriptedModel } from './helpers/scripted-model'

// Company guardrails (guardrail-minimum-plan.md §3.4) on a real backend: the E2E user is
// its company's admin. Sets the policy, makes a company template, edits and publishes it.
// Screens are kept under output/captures/guardrails for review.
const CAPTURE_DIR = path.join('..', 'output', 'captures', 'guardrails')

async function capture(page: Page, name: string, fullPage = true): Promise<void> {
  await fs.mkdir(CAPTURE_DIR, { recursive: true })
  await page.screenshot({ path: path.join(CAPTURE_DIR, name), fullPage })
}

async function homeTenant(page: Page): Promise<string> {
  const rows = await apiGetJson(page.request, `${API_BASE}/api/me/organizations`)
  const list = Array.isArray(rows) ? rows.filter(isRecord) : []
  const home = list.find((row) => row.is_active === true) ?? list[0]
  if (!home || typeof home.tenant_id !== 'string') throw new Error('no organization')
  return home.tenant_id
}

async function putPolicy(
  page: Page,
  policyUrl: string,
  csrf: Record<string, string>,
  data: {
    mode: string
    default_template: string | null
    locked_rules: string[]
    tool_risk?: Record<string, string>
  },
): Promise<void> {
  const current = await apiGetJson(page.request, policyUrl)
  const version = isRecord(current) ? String(current.updated_at) : ''
  await apiJson(
    await page.request.put(policyUrl, {
      headers: { ...csrf, 'If-Match': `"${version.replace(/Z$/, '+00:00')}"` },
      data,
    }),
    'set guardrail policy',
  )
}

test.describe('Company guardrails (company admin)', () => {
  test.skip(process.env.PW_SKIP_BACKEND === '1', 'Requires the FastAPI backend')

  // These tests rewrite the shared company policy; put it back for the specs after them.
  let saved: SavedCompanyPolicy | undefined
  test.beforeAll(async ({ request }) => {
    saved = await saveCompanyPolicy(request)
  })
  test.afterAll(async ({ request }) => {
    if (saved) await restoreCompanyPolicy(request, saved)
  })

  test('sets an enforced policy and publishes a company template', async ({ page }) => {
    const csrf = await loginApi(page.request)
    const tenantId = await homeTenant(page)
    const policyUrl = `${API_BASE}/api/tenants/${tenantId}/guardrails`
    const name = `고객 상담 ${Date.now()}`
    // Start from "agents choose" whatever an earlier run left.
    await putPolicy(page, policyUrl, csrf, {
      mode: 'open',
      default_template: null,
      locked_rules: [],
    })
    // Tall enough for the settings pane, which scrolls inside the page.
    await page.setViewportSize({ width: 1280, height: 1900 })

    await page.goto('/settings/company/guardrails')
    await expect(page.getByRole('button', { name: '강제' })).toBeEnabled({ timeout: 20_000 })
    await capture(page, '01-company-guardrails.png')

    await page.getByRole('button', { name: '강제' }).click()
    await page.getByLabel('기본 템플릿').click()
    await page.getByRole('option', { name: /kr-default/ }).click()
    const locks = page.getByRole('group', { name: '잠근 규칙' })
    const secrets = locks.getByRole('checkbox', { name: '비밀값(API 키·토큰)', exact: true })
    await secrets.click()
    await expect(secrets).toBeChecked()
    await expect(page.getByRole('button', { name: '정책 저장' })).toBeEnabled()
    await capture(page, '02-policy-enforced.png')
    await page.getByRole('button', { name: '정책 저장' }).click()
    await expect
      .poll(async () => apiGetJson(page.request, policyUrl), { timeout: 15_000 })
      .toMatchObject({
        mode: 'enforced',
        default_template: 'builtin:kr-default',
        locked_rules: ['secrets'],
      })

    await page.getByRole('button', { name: '새 템플릿' }).click()
    const create = page.getByRole('dialog')
    await create.getByLabel(/이름/).fill(name)
    await capture(page, '03-template-create.png', false)
    await create.getByRole('button', { name: '만들기' }).click()
    await expect(page.getByRole('button', { name: `${name} 편집` })).toBeVisible({
      timeout: 15_000,
    })

    await page.getByRole('button', { name: `${name} 편집` }).click()
    const editor = page.getByRole('dialog')
    await editor.locator('#guardrail-rule-contacts-action').click()
    await page.getByRole('option', { name: '치환' }).click()
    await capture(page, '04-template-editor.png', false)
    await editor.getByRole('button', { name: '초안 저장' }).click()
    await expect(editor).toBeHidden({ timeout: 15_000 })

    await page.getByRole('button', { name: `${name} 게시` }).click()
    await expect(
      page.getByRole('listitem').filter({ hasText: name }).getByText('v1', { exact: true }),
    ).toBeVisible({ timeout: 15_000 })
    await expect(page.getByRole('button', { name: '강제' })).toHaveAttribute('aria-pressed', 'true')
    await expect(secrets).toBeChecked()
    await capture(page, '05-template-published.png')

    const templates = await apiGetJson(page.request, `${policyUrl}/templates`)
    const made = Array.isArray(templates)
      ? templates.filter(isRecord).find((item) => item.name === name)
      : undefined
    expect(made).toMatchObject({ status: 'published', published_version: 1 })
    const rules = Array.isArray(made?.draft_rules) ? made.draft_rules.filter(isRecord) : []
    expect(rules.find((rule) => rule.id === 'contacts')?.action).toBe('redact')
  })

  test('an agent keeps the locked rule and switches another one off', async ({ page }) => {
    const csrf = await loginApi(page.request)
    const tenantId = await homeTenant(page)
    const policyUrl = `${API_BASE}/api/tenants/${tenantId}/guardrails`
    await putPolicy(page, policyUrl, csrf, {
      mode: 'default',
      default_template: 'builtin:kr-default',
      locked_rules: ['secrets'],
      tool_risk: { external_mutation: 'block' },
    })
    const models = await apiGetJson(page.request, `${API_BASE}/api/models`)
    const model = Array.isArray(models) ? models.find(isSeededScriptedModel) : undefined
    if (!model) throw new Error('no scripted model')
    const agent = await apiPostJson(page.request, `${API_BASE}/api/agents`, csrf, {
      name: `가드레일 에이전트 ${Date.now()}`,
      system_prompt: 'Guardrail card fixture.',
      model_id: model.id,
    })
    const agentId = isRecord(agent) ? String(agent.id) : ''
    const bindingUrl = `${API_BASE}/api/agents/${agentId}/guardrails`

    await page.setViewportSize({ width: 1280, height: 1900 })
    await page.goto(`/agents/${agentId}/settings`)
    await page.getByRole('tab', { name: '설정', exact: true }).click()
    const card = page.getByTestId('agent-guardrail-card')
    await card.scrollIntoViewIfNeeded()
    await expect(card.getByRole('switch', { name: '비밀값(API 키·토큰)' })).toHaveAttribute(
      'aria-disabled',
      'true',
      { timeout: 20_000 },
    )
    const toolRisk = card.getByTestId('agent-tool-risk')
    await expect(toolRisk.getByText('외부 변경')).toBeVisible()
    await expect(toolRisk.getByText('차단')).toBeVisible()
    await card.getByRole('switch', { name: '이름·주소', exact: true }).click()
    await expect(card.getByRole('switch', { name: '이름·주소', exact: true })).not.toBeChecked()
    await card.screenshot({ path: path.join(CAPTURE_DIR, '06-agent-card.png') })
    await card.getByRole('button', { name: '가드레일 저장' }).click()

    await expect
      .poll(async () => apiGetJson(page.request, bindingUrl), { timeout: 15_000 })
      .toMatchObject({
        mode: 'inherit',
        rule_overrides: { people: { enabled: false, action: null } },
      })
    await expect(card.getByTestId('agent-guardrail-effective')).toContainText('kr-default v4')
    await card.getByRole('button', { name: '끔' }).click()
    await expect(card.getByText(/잠근 규칙 1개는 계속 적용됩니다/)).toBeVisible()
    await card.screenshot({ path: path.join(CAPTURE_DIR, '07-agent-card-off.png') })
  })

  test('sets the company action per tool risk level', async ({ page }) => {
    const csrf = await loginApi(page.request)
    const tenantId = await homeTenant(page)
    const policyUrl = `${API_BASE}/api/tenants/${tenantId}/guardrails`
    await putPolicy(page, policyUrl, csrf, {
      mode: 'open',
      default_template: null,
      locked_rules: [],
      tool_risk: {},
    })

    await page.setViewportSize({ width: 1280, height: 1900 })
    await page.goto('/settings/company/guardrails')
    const external = page.getByRole('group', { name: '외부 변경' })
    await external.getByRole('button', { name: '차단' }).click({ timeout: 20_000 })
    await page
      .getByRole('group', { name: '코드 실행' })
      .getByRole('button', { name: '허용' })
      .click()
    await expect(page.getByRole('button', { name: '기본 조치 저장' })).toBeEnabled()
    const card = page
      .getByRole('heading', { name: '도구 위험 등급별 기본 조치' })
      .locator('xpath=ancestor::section[1]')
    await card.screenshot({ path: path.join(CAPTURE_DIR, '08-tool-risk.png') })
    await page.getByRole('button', { name: '기본 조치 저장' }).click()

    await expect
      .poll(async () => apiGetJson(page.request, policyUrl), { timeout: 15_000 })
      .toMatchObject({
        mode: 'open',
        tool_risk: {
          read_only: 'allow',
          write_internal: 'approval',
          external_mutation: 'block',
          code_execution: 'allow',
          unknown: 'approval',
        },
      })
  })
})
