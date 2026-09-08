import { randomUUID } from 'node:crypto'
import type { Page } from '@playwright/test'
import { z } from 'zod'

import {
  API_BASE,
  apiDeleteOk,
  apiGetJson,
  apiJson,
  apiPostJson,
  loginApi,
  test,
  expect,
} from './fixtures'
import { registerMember } from './helpers/member-session'
import { startMcpAppsFixture } from './helpers/mcp-apps-fixture'
import { captureResourcePage } from './helpers/capture-resource-page'
import { ONBOARDING_DISMISSED_FLAG, SUPER_USER_WELCOMED_FLAG } from '../src/lib/auth/session-flags'

const itemSchema = z.object({
  id: z.string(),
  name: z.string(),
  resource_type: z.enum(['agent', 'mcp', 'skill']),
  visibility: z.string(),
  is_listed: z.boolean(),
  status: z.string(),
})
const idSchema = z.object({ id: z.string() })

async function publishPublic(page: Page, name: string, sourcePath: string) {
  const dialog = page.getByRole('dialog', { name: `${name} 게시` })
  await dialog.getByRole('button', { name: '다음', exact: true }).click()
  await expect(dialog.getByLabel('이름', { exact: true })).toHaveValue(name)
  await dialog.getByLabel('설명', { exact: true }).fill('Isolated E2E publication')
  await dialog.getByRole('button', { name: '다음', exact: true }).click()
  await dialog.getByRole('combobox').click()
  await page.getByRole('option', { name: '공개 (리스팅 대기)', exact: true }).click()
  await dialog.getByRole('button', { name: '다음', exact: true }).click()
  const published = page.waitForResponse(
    (res) => res.url().endsWith(sourcePath) && res.request().method() === 'POST',
  )
  const versionsLoaded = page.waitForResponse(
    (res) =>
      /\/api\/marketplace\/items\/[^/]+\/versions$/.test(res.url()) &&
      res.request().method() === 'GET',
  )
  await dialog.getByRole('button', { name: '게시', exact: true }).click()
  const item = itemSchema.parse(await apiJson(await published, 'Publish marketplace item'))
  expect(item).toMatchObject({ name, visibility: 'public', is_listed: false, status: 'published' })
  await expect(page).toHaveURL(new RegExp(`/marketplace/${item.id}$`))
  await apiJson(await versionsLoaded, 'Load published versions')
  await expect(page.getByRole('heading', { name, exact: true })).toBeVisible()
  return item
}

test('agent UI publication waits for operator approval and becomes listed', async ({
  page,
  request,
  errors,
}, testInfo) => {
  const csrf = await loginApi(request)
  const name = `E2E Published Agent ${randomUUID()}`
  const models = z
    .array(idSchema.extend({ provider: z.string() }))
    .parse(await apiGetJson(request, `${API_BASE}/api/models`))
  const model = models.find((candidate) => candidate.provider === 'e2e_scripted')
  if (!model) throw new Error('Scripted model is not seeded')
  const agent = idSchema.parse(
    await apiPostJson(request, `${API_BASE}/api/agents`, csrf, {
      name,
      model_id: model.id,
      system_prompt: 'Portable agent publication fixture.',
    }),
  )
  let itemId: string | undefined
  try {
    await page.addInitScript(
      ({ onboarding, welcome }) => {
        sessionStorage.setItem(onboarding, '1')
        sessionStorage.setItem(welcome, '1')
      },
      { onboarding: ONBOARDING_DISMISSED_FLAG, welcome: SUPER_USER_WELCOMED_FLAG },
    )
    await page.goto('/')
    const card = page
      .getByRole('main')
      .locator(`a[href="/agents/${agent.id}"]`)
      .filter({ hasText: name })
    await card.hover()
    await card.getByRole('button', { name: '마켓플레이스에 게시', exact: true }).click()
    const item = await publishPublic(page, name, `/api/marketplace/items/from-agent/${agent.id}`)
    itemId = item.id
    expect(item.resource_type).toBe('agent')
    await page.goto('/settings/marketplace-admin')
    const sidebarNavigationBoundary = page.locator('[data-sidebar="content"]')
    const adminNavigationTail = page.getByRole('link', {
      name: '전체 활동 기록',
      exact: true,
    })
    const row = page
      .getByRole('listitem')
      .filter({ has: page.getByRole('link', { name, exact: true }) })
    await expect(row).toBeVisible()
    await captureResourcePage({
      page,
      testInfo,
      state: 'marketplace-agent-pending',
      evidence: row,
      verticalContainment: {
        target: adminNavigationTail,
        boundary: sidebarNavigationBoundary,
      },
    })
    const approved = page.waitForResponse(
      (res) =>
        res.url().endsWith(`/api/marketplace/admin/items/${item.id}/listed`) &&
        res.request().method() === 'POST',
    )
    await row.getByRole('button', { name: '승인', exact: true }).click()
    expect(itemSchema.parse(await apiJson(await approved, 'Approve listing')).is_listed).toBe(true)
    await expect(row).toHaveCount(0)
    await page.reload()
    await expect(
      page.getByRole('heading', { name: '마켓플레이스 운영', exact: true }),
    ).toBeVisible()
    await expect(row).toHaveCount(0)
    const listed = z
      .array(itemSchema)
      .parse(
        await apiGetJson(
          request,
          `${API_BASE}/api/marketplace/items?is_listed=true&resource_type=agent&q=${encodeURIComponent(name)}`,
        ),
      )
    expect(listed.map((entry) => entry.id)).toContain(item.id)
    await captureResourcePage({
      page,
      testInfo,
      state: 'marketplace-agent-approved',
      evidence: page.getByRole('heading', { name: '마켓플레이스 운영', exact: true }),
      verticalContainment: {
        target: adminNavigationTail,
        boundary: sidebarNavigationBoundary,
      },
    })
    expect(errors.console).toEqual([])
    expect(errors.network).toEqual([])
  } finally {
    if (itemId)
      await apiPostJson(request, `${API_BASE}/api/marketplace/items/${itemId}/disable`, csrf)
    await apiDeleteOk(request, `${API_BASE}/api/agents/${agent.id}`, csrf)
  }
})

test('MCP UI publication can be disabled by moderation and stays unlisted', async ({
  page,
  request,
  errors,
}, testInfo) => {
  const fixture = await startMcpAppsFixture()
  const csrf = await loginApi(request)
  const name = `E2E Published MCP ${randomUUID()}`
  let serverId: string | undefined
  let itemId: string | undefined
  try {
    serverId = idSchema.parse(
      await apiPostJson(request, `${API_BASE}/api/mcp-servers`, csrf, {
        name,
        transport: 'streamable_http',
        url: fixture.url,
      }),
    ).id
    await page.goto('/mcp-servers')
    const card = page.getByRole('article').filter({ hasText: name })
    await card.getByRole('button', { name: '게시', exact: true }).click()
    const item = await publishPublic(page, name, `/api/marketplace/items/from-mcp/${serverId}`)
    itemId = item.id
    expect(item.resource_type).toBe('mcp')
    await page.goto('/settings/marketplace-admin')
    const sidebarNavigationBoundary = page.locator('[data-sidebar="content"]')
    const adminNavigationTail = page.getByRole('link', {
      name: '전체 활동 기록',
      exact: true,
    })
    const row = page
      .getByRole('listitem')
      .filter({ has: page.getByRole('link', { name, exact: true }) })
    await captureResourcePage({
      page,
      testInfo,
      state: 'marketplace-mcp-pending',
      evidence: row,
      verticalContainment: {
        target: adminNavigationTail,
        boundary: sidebarNavigationBoundary,
      },
    })
    const disabled = page.waitForResponse(
      (res) =>
        res.url().endsWith(`/api/marketplace/items/${item.id}/disable`) &&
        res.request().method() === 'POST',
    )
    await row.getByRole('button', { name: '비활성화', exact: true }).click()
    expect(itemSchema.parse(await apiJson(await disabled, 'Disable publication'))).toMatchObject({
      status: 'disabled',
      is_listed: false,
    })
    await expect(row).toHaveCount(0)
    await page.reload()
    await expect(
      page.getByRole('heading', { name: '마켓플레이스 운영', exact: true }),
    ).toBeVisible()
    await expect(row).toHaveCount(0)
    await captureResourcePage({
      page,
      testInfo,
      state: 'marketplace-mcp-disabled',
      evidence: page.getByRole('heading', { name: '마켓플레이스 운영', exact: true }),
      verticalContainment: {
        target: adminNavigationTail,
        boundary: sidebarNavigationBoundary,
      },
    })
    expect(
      itemSchema.parse(await apiGetJson(request, `${API_BASE}/api/marketplace/items/${item.id}`)),
    ).toMatchObject({ status: 'disabled', is_listed: false })
    expect(errors.console).toEqual([])
    expect(errors.network).toEqual([])
  } finally {
    try {
      if (itemId)
        await apiPostJson(request, `${API_BASE}/api/marketplace/items/${itemId}/disable`, csrf)
      if (serverId) await apiDeleteOk(request, `${API_BASE}/api/mcp-servers/${serverId}`, csrf)
    } finally {
      await fixture.stop()
    }
  }
})

test('ordinary member cannot access moderation controls or listing approval API', async ({
  page,
  errors,
}, testInfo) => {
  const member = await registerMember(page)
  await page.goto('/settings/marketplace-admin')
  await expect(page.getByText('운영자 전용 페이지', { exact: true })).toBeVisible()
  await expect(page.getByRole('button', { name: '승인', exact: true })).toHaveCount(0)
  await captureResourcePage({
    page,
    testInfo,
    state: 'marketplace-member-denied',
    evidence: page.getByText('운영자 전용 페이지', { exact: true }),
  })
  const denied = await page.request.post(
    `${API_BASE}/api/marketplace/admin/items/${randomUUID()}/listed`,
    {
      headers: { 'X-CSRF-Token': member.csrf_token },
      data: { is_listed: true },
    },
  )
  expect(denied.status()).toBe(403)
  expect(errors.console).toEqual([])
  expect(errors.network).toEqual([])
})
