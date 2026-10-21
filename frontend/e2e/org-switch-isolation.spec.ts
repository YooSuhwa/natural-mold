import type { APIRequestContext } from '@playwright/test'

import { API_BASE, apiGetJson, apiPostJson, isRecord, loginApi, test, expect } from './fixtures'

// Org-authz B-07/B-09 on a real backend: a member of two organizations of one company
// switches with the sidebar switcher and sees only the active organization's agents
// (no stale list from the previous organization after the switch resets client state).

interface OrgRow {
  id: string
  name: string
  tenant_id: string
  is_active: boolean
}

function orgRows(value: unknown): OrgRow[] {
  if (!Array.isArray(value)) throw new Error('organizations response is not a list')
  return value.filter(
    (row): row is OrgRow =>
      isRecord(row) &&
      typeof row.id === 'string' &&
      typeof row.name === 'string' &&
      typeof row.tenant_id === 'string',
  )
}

async function scriptedModelId(request: APIRequestContext): Promise<string> {
  const models = await apiGetJson(request, `${API_BASE}/api/models`)
  const model = Array.isArray(models)
    ? models.find((row) => isRecord(row) && row.provider === 'e2e_scripted')
    : undefined
  if (!isRecord(model) || typeof model.id !== 'string') throw new Error('no scripted model')
  return model.id
}

function idOf(value: unknown, label: string): string {
  if (!isRecord(value) || typeof value.id !== 'string') throw new Error(`${label}: no id`)
  return value.id
}

test.describe('Organization switch isolation (org-authz B-07/B-09)', () => {
  test('the switcher shows only the active organization agents', async ({ page }) => {
    const request = page.request
    const csrf = await loginApi(request)
    const orgs = orgRows(await apiGetJson(request, `${API_BASE}/api/me/organizations`))
    const home = orgs.find((row) => row.is_active) ?? orgs[0]
    if (!home) throw new Error('the E2E user has no organization')
    const me = await apiGetJson(request, `${API_BASE}/api/auth/me`)
    const unique = Date.now()
    const otherName = `E2E 전환 조직 ${unique}`
    const created = await apiPostJson(
      request,
      `${API_BASE}/api/tenants/${home.tenant_id}/organizations`,
      csrf,
      { name: otherName, administrator: { user_id: idOf(me, 'me') } },
    )
    const otherId = isRecord(created) ? idOf(created.organization, 'organization') : ''
    const modelId = await scriptedModelId(request)
    const agent = (name: string) =>
      apiPostJson(request, `${API_BASE}/api/agents`, csrf, {
        name,
        system_prompt: 'Organization switch fixture.',
        model_id: modelId,
      })
    const homeAgent = `E2E home org agent ${unique}`
    const otherAgent = `E2E other org agent ${unique}`
    await agent(homeAgent)
    await apiPostJson(request, `${API_BASE}/api/me/active-org`, csrf, { org_id: otherId })
    await agent(otherAgent)
    await apiPostJson(request, `${API_BASE}/api/me/active-org`, csrf, { org_id: home.id })

    await page.goto('/agents')
    await expect(page.getByText(homeAgent).first()).toBeVisible({ timeout: 30_000 })
    await expect(page.getByText(otherAgent)).toHaveCount(0)

    await page.getByTestId('organization-switcher-trigger').click()
    await page.getByRole('menuitem', { name: new RegExp(otherName) }).click()
    await expect(page.getByTestId('organization-switcher-trigger')).toContainText(otherName)

    await page.goto('/agents')
    await expect(page.getByText(otherAgent).first()).toBeVisible({ timeout: 30_000 })
    await expect(page.getByText(homeAgent)).toHaveCount(0)

    // Leave the shared suite login in its home organization.
    await apiPostJson(request, `${API_BASE}/api/me/active-org`, csrf, { org_id: home.id })
  })
})
