import { randomUUID } from 'node:crypto'
import type { APIRequestContext, Page } from '@playwright/test'
import { z } from 'zod'

import {
  API_BASE,
  apiGetJson,
  apiJson,
  apiPostJson,
  expect,
  loginApi,
  test,
  type CsrfHeaders,
} from './fixtures'
import { isSeededScriptedModel } from './helpers/scripted-model'
import { sendMessageForRun, waitForRunStatus } from './langgraph-v3-helpers'

const id = z.object({ id: z.string() })
const version = id.extend({ version_label: z.string() })
const installation = id.extend({ version_id: z.string(), installed_skill_id: z.string() })
const source = {
  title: 'Updated research bulletin',
  url: 'https://evidence.example.org/reports/research-update',
  provider: 'searxng',
}
const fixtureReport = z.object({
  calls: z.array(z.object({ provider: z.string(), status: z.number(), time_range: z.string() })),
  published_date: z.string(),
})

async function setProviders(
  request: APIRequestContext,
  csrf: CsrfHeaders,
  orgId: string,
  providers: string[],
) {
  const url = `${API_BASE}/api/orgs/${orgId}/research-search-policy`
  const current = await request.get(url)
  expect(current.status()).toBe(200)
  const etag = current.headers()['etag']
  expect(etag).toBeTruthy()
  await apiJson(
    await request.put(url, {
      headers: { ...csrf, 'If-Match': etag },
      data: {
        providers,
        monthly_attempt_limit: 4,
        searxng_endpoint: providers.includes('searxng')
          ? 'https://searxng.example.test/search'
          : null,
      },
    }),
    'Configure research providers',
  )
}

async function searchTurn(page: Page, csrf: CsrfHeaders, agentId: string, marker: string) {
  const conversation = id.parse(
    await apiPostJson(page.request, `${API_BASE}/api/agents/${agentId}/conversations`, csrf, {
      title: marker,
    }),
  )
  await page.goto(`/agents/${agentId}/conversations/${conversation.id}`)
  const runId = await sendMessageForRun(page, conversation.id, marker)
  await waitForRunStatus(page.request, conversation.id, runId, 'completed', 90_000)
  const envelope = z
    .object({
      messages: z.array(
        z.object({
          role: z.string(),
          content: z.string(),
          tool_call_id: z.string().nullable(),
          tool_calls: z.array(z.object({ id: z.string(), name: z.string() })).nullable(),
        }),
      ),
    })
    .parse(
      await apiGetJson(page.request, `${API_BASE}/api/conversations/${conversation.id}/messages`),
    )
  const toolCalls = envelope.messages.flatMap((message) => message.tool_calls ?? [])
  expect(toolCalls.map((call) => call.name)).toEqual(['read_file', 'research_search'])
  const skillFile = envelope.messages.find(
    (message) => message.role === 'tool' && message.tool_call_id === toolCalls[0].id,
  )
  expect(skillFile?.content).toContain('0.2.0')
  const calls = toolCalls.filter((call) => call.name === 'research_search')
  expect(calls).toHaveLength(1)
  const output = envelope.messages.find(
    (message) => message.role === 'tool' && message.tool_call_id === calls[0].id,
  )
  if (!output) throw new Error('Stored tool result missing from the conversation')
  return { runId, result: output.content }
}

test('an upgraded Deep Research skill executes authorized fallback and restores its original citation', async ({
  page,
}, testInfo) => {
  expect(process.env.E2E_RESEARCH_FIXTURE, 'Run scripts/run-research-search-e2e.sh').toBe('1')
  test.setTimeout(240_000)
  expect(['127.0.0.1', 'localhost']).toContain(new URL(API_BASE).hostname)
  const port = Number(process.env.E2E_RESEARCH_FIXTURE_PORT)
  expect(port).toBeGreaterThan(0)
  const observations = () =>
    apiGetJson(page.request, `http://127.0.0.1:${port}/observations`).then((value) =>
      fixtureReport.parse(value),
    )
  const baseline = await observations()
  const request = page.request
  const csrf = await loginApi(request)
  const orgs = z
    .array(id.extend({ is_active: z.boolean(), tenant_id: z.string() }))
    .parse(await apiGetJson(request, `${API_BASE}/api/me/organizations`))
  const home = orgs.find((org) => org.is_active)
  if (!home) throw new Error('No active organization for the isolated test account')
  const me = id.parse(await apiGetJson(request, `${API_BASE}/api/auth/me`))
  const org = z.object({ organization: id }).parse(
    await apiPostJson(request, `${API_BASE}/api/tenants/${home.tenant_id}/organizations`, csrf, {
      name: `Research fixture ${randomUUID()}`,
      administrator: { user_id: me.id },
    }),
  ).organization
  await apiPostJson(request, `${API_BASE}/api/me/active-org`, csrf, { org_id: org.id })
  try {
    await setProviders(request, csrf, org.id, ['tavily', 'searxng'])
    const items = z
      .array(id.extend({ slug: z.string() }))
      .parse(
        await apiGetJson(
          request,
          `${API_BASE}/api/marketplace/items?resource_type=skill&source_kind=system_seed&limit=200`,
        ),
      )
    const item = items.find((row) => row.slug === 'deep-research')
    if (!item) throw new Error('Deep Research seed not found')
    const detailUrl = `${API_BASE}/api/marketplace/items/${item.id}`
    const versions = z.array(version).parse(await apiGetJson(request, `${detailUrl}/versions`))
    const old = versions.find((row) => row.version_label === '0.1.0')
    const next = versions.find((row) => row.version_label === '0.2.0')
    if (!old || !next) throw new Error('Both published versions are required for the upgrade')
    const installed = installation.parse(
      await apiPostJson(request, `${detailUrl}/install`, csrf, {
        version_id: old.id,
      }),
    )
    const skillUrl = `${API_BASE}/api/skills/${installed.installed_skill_id}`
    expect(installed.version_id).toBe(old.id)
    expect(await apiGetJson(request, skillUrl)).toMatchObject({
      version: '0.1.0',
      execution_profile: { tool_dependencies: ['tavily_search'] },
    })
    expect(await apiGetJson(request, detailUrl)).toMatchObject({
      installation: { update_available: true },
    })
    await page.goto(`/marketplace/${item.id}`)
    await page.getByRole('button', { name: '업데이트', exact: true }).click()
    const dialog = page.getByRole('dialog', { name: 'Deep Research 업데이트' })
    const pending = page.waitForResponse(
      (response) =>
        response.request().method() === 'POST' &&
        response.url().endsWith(`/installations/${installed.id}/update`),
    )
    await dialog.getByRole('button', { name: '확인', exact: true }).click()
    const updated = installation.parse(
      await apiJson(await pending, 'Apply the selected marketplace update'),
    )
    expect(updated).toMatchObject({
      id: installed.id,
      installed_skill_id: installed.installed_skill_id,
      version_id: next.id,
    })
    expect(await apiGetJson(request, skillUrl)).toMatchObject({
      version: '0.2.0',
      execution_profile: { tool_dependencies: ['research_search'] },
    })

    const models = await apiGetJson(request, `${API_BASE}/api/models`)
    const model = Array.isArray(models) && models.find(isSeededScriptedModel)
    if (!model) throw new Error('Expected the local scripted model')
    const agent = id.parse(
      await apiPostJson(request, `${API_BASE}/api/agents`, csrf, {
        name: 'Updated Deep Research fixture',
        model_id: model.id,
        skill_ids: [updated.installed_skill_id],
        tool_ids: [],
        system_prompt: 'Use the attached Deep Research skill to find sources.',
      }),
    )
    const completed = await searchTurn(page, csrf, agent.id, 'E2E_RESEARCH_FALLBACK')
    const observed = await observations()
    expect(observed.calls.slice(baseline.calls.length)).toEqual([
      { provider: 'tavily', status: 503, time_range: 'month' },
      { provider: 'searxng', status: 200, time_range: 'month' },
    ])
    const result = z
      .object({
        provider: z.literal('searxng'),
        time_range: z.literal('month'),
        results: z.array(
          z.object({
            title: z.string(),
            url: z.string(),
            published_date: z.string(),
            provider: z.string(),
          }),
        ),
      })
      .parse(JSON.parse(completed.result))
    expect(result.results).toHaveLength(1)
    expect(result.results[0]).toMatchObject(source)
    expect(Date.parse(result.results[0].published_date)).toBe(Date.parse(observed.published_date))
    const published = new Intl.DateTimeFormat('ko-KR', {
      timeZone: 'Asia/Seoul',
      dateStyle: 'medium',
    }).format(new Date(observed.published_date))
    async function expectCitation() {
      await expect(page.getByText('SearXNG · 1건', { exact: true })).toBeVisible()
      await expect(page.getByRole('link', { name: source.title })).toHaveAttribute(
        'href',
        source.url,
      )
      await expect(page.getByText(`발행일 ${published}`, { exact: true })).toBeVisible()
    }
    await expectCitation()
    await page.reload()
    await expectCitation()
    expect((await observations()).calls).toEqual(observed.calls)

    await setProviders(request, csrf, org.id, ['searxng'])
    const unsupported = await searchTurn(page, csrf, agent.id, 'E2E_RESEARCH_WEEK')
    expect(unsupported.result).toMatch(/requested period/i)
    expect((await observations()).calls).toEqual(observed.calls)
    await setProviders(request, csrf, org.id, [])
    const blocked = await searchTurn(page, csrf, agent.id, 'E2E_RESEARCH_BLOCKED')
    expect(blocked.result).toMatch(/not enabled/i)
    expect((await observations()).calls).toEqual(observed.calls)
    await testInfo.attach('research-search-acceptance', {
      contentType: 'application/json',
      body: JSON.stringify({
        installedVersion: old.version_label,
        upgradedVersion: next.version_label,
        runs: [completed.runId, unsupported.runId, blocked.runId],
        calls: observed.calls.slice(baseline.calls.length),
        citation: result.results[0],
        noDispatchCases: ['unsupported week', 'no approved provider'],
      }),
    })
  } finally {
    await apiPostJson(request, `${API_BASE}/api/me/active-org`, csrf, { org_id: home.id })
  }
})
