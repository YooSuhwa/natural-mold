import { test, expect } from './fixtures'
import type { APIRequestContext } from '@playwright/test'

import { isSeededScriptedModel } from './helpers/scripted-model'

// workflow-starts-sharing-plan: schedules and webhooks on the runs page, end to end on the
// keyless scripted model and the job worker. A schedule is made, paused and deleted on the page
// (firing is covered by the backend tests: a sweep cannot be waited for here); a webhook is made
// on the page and an outside caller starts a run with the token shown once, then reads its
// result with the same token.
const API =
  process.env.E2E_API_BASE_URL ?? `http://localhost:${process.env.E2E_BACKEND_PORT ?? '8001'}`
const EMAIL = process.env.E2E_USER_EMAIL ?? process.env.E2E_EMAIL ?? 'playwright-e2e@moldy.dev'
const PASSWORD =
  process.env.E2E_USER_PASSWORD ?? process.env.E2E_PASSWORD ?? 'correct horse battery staple 42'
const SLOW = { timeout: 45_000 }

async function login(request: APIRequestContext): Promise<Record<string, string>> {
  const res = await request.post(`${API}/api/auth/login`, {
    data: { email: EMAIL, password: PASSWORD },
  })
  expect(res.ok()).toBeTruthy()
  return { 'X-CSRF-Token': (await res.json()).csrf_token as string }
}

async function send<T>(
  request: APIRequestContext,
  csrf: Record<string, string>,
  url: string,
  data: unknown,
  method: 'post' | 'put' = 'post',
): Promise<T> {
  const res = await request[method](`${API}${url}`, { headers: csrf, data })
  expect(
    res.ok(),
    `${method.toUpperCase()} ${url} → ${res.status()} ${await res.text()}`,
  ).toBeTruthy()
  return (await res.json()) as T
}

test.describe('workflow starts — schedules and webhooks', () => {
  test.skip(process.env.PW_SKIP_BACKEND === '1', 'Requires the FastAPI backend')
  test.setTimeout(180_000)

  let workflowId = ''

  test.beforeAll(async ({ request }) => {
    const csrf = await login(request)
    const models = (await (await request.get(`${API}/api/models`)).json()) as unknown[]
    const scripted = models.find(isSeededScriptedModel)
    if (!scripted) throw new Error('the seeded scripted model should exist')
    const stamp = Date.now()
    const agent = await send<{ id: string }>(request, csrf, '/api/agents', {
      name: `E2E 요약 ${stamp}`,
      system_prompt: '요약합니다.',
      model_id: scripted.id,
    })
    const created = await send<{ id: string; draft_revision: number }>(
      request,
      csrf,
      '/api/workflows',
      { name: `E2E 시작 경로 ${stamp}` },
    )
    workflowId = created.id
    const definition = {
      schema_version: 'moldy.workflow.v1',
      inputs: [{ name: 'topic', type: 'string', required: true, label: '주제' }],
      nodes: [
        { id: 'start', type: 'start' },
        {
          id: 'sum',
          type: 'agent',
          label: '요약',
          agent_id: agent.id,
          prompt: [{ text: '요약: ' }, { ref: { node: 'input', path: '/topic' } }],
          output_schema: {
            type: 'object',
            properties: { summary: { type: 'string' } },
            required: ['summary'],
          },
        },
        {
          id: 'done',
          type: 'output',
          fields: { summary: [{ ref: { node: 'sum', path: '/summary' } }] },
        },
      ],
      edges: [
        { from: 'start', to: 'sum' },
        { from: 'sum', to: 'done' },
      ],
    }
    const saved = await send<{ draft_revision: number }>(
      request,
      csrf,
      `/api/workflows/${workflowId}/draft`,
      { revision: created.draft_revision, definition },
      'put',
    )
    await send(request, csrf, `/api/workflows/${workflowId}/publish`, {
      revision: saved.draft_revision,
    })
  })

  test('a schedule is made, paused and deleted on the runs page', async ({ page }) => {
    await page.goto(`/workflows/${workflowId}/runs`)
    const panel = page.getByRole('region', { name: '예약' })
    await panel.getByRole('button', { name: '예약 추가' }).click()
    const form = panel.getByRole('form', { name: '새 예약' })
    await form.getByLabel('예약 이름').fill('아침 요약')
    await form.getByLabel('시각').fill('08:30')
    await form.getByLabel(/주제/).fill('오늘 뉴스')
    await form.getByRole('button', { name: '예약 저장' }).click()
    const list = panel.getByRole('list', { name: '예약 목록' })
    await expect(list).toContainText('아침 요약', SLOW)
    await expect(list).toContainText('매일 08:30')
    await expect(list).toContainText('켜짐')
    await list.getByRole('button', { name: '멈추기' }).click()
    await expect(list).toContainText('멈춤', SLOW)
    await list.getByRole('button', { name: '아침 요약 예약 지우기' }).click()
    await expect(list).not.toContainText('아침 요약', SLOW)
  })

  test('a webhook made on the page starts a run from outside', async ({ page, request }) => {
    await page.goto(`/workflows/${workflowId}/runs`)
    const panel = page.getByRole('region', { name: '웹훅' })
    await panel.getByLabel('웹훅 이름').fill('주문 시스템')
    await panel.getByRole('button', { name: '웹훅 만들기' }).click()
    const token = (await panel.getByTestId('webhook-token').textContent({ timeout: 15_000 })) ?? ''
    expect(token.startsWith('mwh_')).toBe(true)
    const address = (
      (await panel
        .getByRole('list', { name: '웹훅 목록' })
        .locator('code')
        .first()
        .textContent()) ?? ''
    ).trim()
    const publicId = address.split('/hooks/workflows/')[1]?.split('/')[0] ?? ''
    await panel.getByRole('button', { name: '보관했어요' }).click()
    await expect(panel.getByTestId('webhook-token')).toHaveCount(0)

    // The outside caller: no session, only the token.
    const outside = await request.post(`${API}/hooks/workflows/${publicId}/runs`, {
      headers: { Authorization: `Bearer ${token}` },
      data: { inputs: { topic: '배송 지연' } },
    })
    expect(outside.status()).toBe(202)
    const runId = (await outside.json()).run_id as string
    await expect
      .poll(
        async () =>
          (
            await (
              await request.get(`${API}/hooks/workflows/${publicId}/runs/${runId}`, {
                headers: { Authorization: `Bearer ${token}` },
              })
            ).json()
          ).status,
        SLOW,
      )
      .toBe('completed')
    const wrong = await request.get(`${API}/hooks/workflows/${publicId}/runs/${runId}`, {
      headers: { Authorization: 'Bearer nope' },
    })
    expect(wrong.status()).toBe(404)
    await page.reload()
    await expect(
      page.locator(`a[href="/workflows/${workflowId}/runs/${runId}"]`).getByText('웹훅'),
    ).toBeVisible(SLOW)
  })
})
