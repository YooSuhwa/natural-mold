import { randomUUID } from 'node:crypto'
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
import { startMcpAppsFixture } from './helpers/mcp-apps-fixture'
import { captureResourcePage } from './helpers/capture-resource-page'

const idSchema = z.object({ id: z.string() })
const agentSchema = z.object({ mcp_tools: z.array(idSchema) })

test('discovered MCP tool attaches, survives reload and detaches from agent settings', async ({
  page,
  request,
  errors,
}, testInfo) => {
  const fixture = await startMcpAppsFixture()
  const csrf = await loginApi(request)
  const serverName = `E2E Attach MCP ${randomUUID()}`
  let serverId: string | undefined
  let agentId: string | undefined
  try {
    serverId = idSchema.parse(
      await apiPostJson(request, `${API_BASE}/api/mcp-servers`, csrf, {
        name: serverName,
        transport: 'streamable_http',
        url: fixture.url,
      }),
    ).id
    const discovered = z
      .object({ tools: z.array(idSchema.extend({ name: z.string() })) })
      .parse(await apiPostJson(request, `${API_BASE}/api/mcp-servers/${serverId}/discover`, csrf))
    const weather = discovered.tools.find((tool) => tool.name === 'weather')
    if (!weather) throw new Error('Local MCP discovery did not return weather')
    const models = z
      .array(idSchema.extend({ provider: z.string() }))
      .parse(await apiGetJson(request, `${API_BASE}/api/models`))
    const model = models.find((candidate) => candidate.provider === 'e2e_scripted')
    if (!model) throw new Error('Scripted model is not seeded')
    agentId = idSchema.parse(
      await apiPostJson(request, `${API_BASE}/api/agents`, csrf, {
        name: `E2E MCP agent ${randomUUID()}`,
        model_id: model.id,
        system_prompt: 'Use weather.',
      }),
    ).id
    const agentUrl = `${API_BASE}/api/agents/${agentId}`
    await page.goto(`/agents/${agentId}/settings`)
    await page.getByRole('button', { name: '추가', exact: true }).first().click()
    const dialog = page.getByRole('dialog')
    await dialog.getByRole('tab', { name: 'MCP', exact: true }).click()
    await dialog
      .getByRole('tabpanel', { name: 'MCP', exact: true })
      .getByPlaceholder('검색', { exact: true })
      .fill(serverName)
    await dialog.getByRole('button', { name: 'weather 추가', exact: true }).click()
    await expect(dialog.getByRole('button', { name: 'weather 제거', exact: true })).toBeVisible()
    await dialog.getByRole('button', { name: '닫기', exact: true }).click()
    const saved = page.waitForResponse(
      (res) => res.url() === agentUrl && res.request().method() === 'PUT',
      { timeout: 15_000 },
    )
    await page.getByRole('button', { name: '저장', exact: true }).click()
    expect(agentSchema.parse(await apiJson(await saved, 'Attach MCP')).mcp_tools).toEqual([
      expect.objectContaining({ id: weather.id }),
    ])

    await page.reload()
    await page.getByRole('button', { name: '추가', exact: true }).first().click()
    await expect(dialog.getByRole('button', { name: 'weather 제거', exact: true })).toBeVisible()
    await captureResourcePage(
      page,
      testInfo,
      'agent-mcp-attached',
      dialog.getByRole('button', { name: 'weather 제거', exact: true }),
    )
    await dialog.getByRole('button', { name: 'weather 제거', exact: true }).click()
    await dialog.getByRole('button', { name: '닫기', exact: true }).click()
    const detached = page.waitForResponse(
      (res) => res.url() === agentUrl && res.request().method() === 'PUT',
      { timeout: 15_000 },
    )
    await page.getByRole('button', { name: '저장', exact: true }).click()
    expect(agentSchema.parse(await apiJson(await detached, 'Detach MCP')).mcp_tools).toEqual([])
    expect(agentSchema.parse(await apiGetJson(request, agentUrl)).mcp_tools).toEqual([])
    expect(errors.console).toEqual([])
    expect(errors.network).toEqual([])
  } finally {
    try {
      if (agentId) await apiDeleteOk(request, `${API_BASE}/api/agents/${agentId}`, csrf)
      if (serverId) await apiDeleteOk(request, `${API_BASE}/api/mcp-servers/${serverId}`, csrf)
    } finally {
      await fixture.stop()
    }
  }
})
