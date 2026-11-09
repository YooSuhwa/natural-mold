import { test, expect } from './fixtures'
import type { APIRequestContext } from '@playwright/test'

import { isSeededScriptedModel } from './helpers/scripted-model'

// workflow-nodes-plan: every remaining node kind in one published workflow, end to end on the
// keyless scripted model and the job worker. The scripted agent lists two topics (it answers
// arrays with two items), so the for_each step runs two children; the parallel step runs two
// agents; the llm step and the llm decision use the system model (the question carries
// E2E_WORKFLOW_YES, so it says yes); the switch takes the "news" case for the topic "뉴스";
// the ask step waits for the runner's written answer, given on the run page; notify sends one
// notification; the tool step reads the current time.
const API =
  process.env.E2E_API_BASE_URL ?? `http://localhost:${process.env.E2E_BACKEND_PORT ?? '8001'}`
const EMAIL = process.env.E2E_USER_EMAIL ?? process.env.E2E_EMAIL ?? 'playwright-e2e@moldy.dev'
const PASSWORD =
  process.env.E2E_USER_PASSWORD ?? process.env.E2E_PASSWORD ?? 'correct horse battery staple 42'
const SLOW = { timeout: 45_000 }

type Step = {
  id: string
  node_id: string
  status: string
  branch: string | null
  parent_step_id: string | null
  output: Record<string, unknown> | null
}
type Run = { id: string; status: string; output: Record<string, unknown> | null; steps: Step[] }

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

const ref = (node: string, path = '') => ({ ref: { node, path } })
const text = (value: string) => ({ text: value })
const SUMMARY = {
  type: 'object',
  properties: { summary: { type: 'string' } },
  required: ['summary'],
}

function everyNode(agentId: string) {
  return {
    schema_version: 'moldy.workflow.v1',
    inputs: [{ name: 'topic', type: 'string', required: true, label: '주제' }],
    nodes: [
      { id: 'start', type: 'start' },
      {
        id: 'lister',
        type: 'agent',
        label: '주제 나누기',
        agent_id: agentId,
        prompt: [text('나눌 주제: '), ref('input', '/topic')],
        output_schema: {
          type: 'object',
          properties: { topics: { type: 'array', items: { type: 'string' } } },
          required: ['topics'],
        },
      },
      {
        id: 'each',
        type: 'for_each',
        label: '주제마다 조사',
        source: 'lister',
        path: '/topics',
        agent_id: agentId,
        prompt: [text('조사: '), ref('item')],
        output_schema: SUMMARY,
        max_items: 5,
      },
      {
        id: 'views',
        type: 'parallel',
        label: '찬반 검토',
        branches: ['pro', 'con'].map((name) => ({
          name,
          agent_id: agentId,
          prompt: [text(`${name} 관점: `), ref('each', '/count')],
          output_schema: SUMMARY,
        })),
      },
      {
        id: 'sum',
        type: 'llm',
        label: '요약',
        prompt: [text('요약: '), ref('views', '/pro/summary')],
        output_schema: SUMMARY,
      },
      {
        id: 'enough',
        type: 'decision_bool',
        label: '충분한가',
        evaluator: 'llm',
        question: [text('E2E_WORKFLOW_YES 충분한가? '), ref('sum', '/summary')],
      },
      {
        id: 'route',
        type: 'switch',
        label: '분야',
        source: 'input',
        path: '/topic',
        cases: [{ name: 'news', value: '뉴스' }],
      },
      {
        id: 'ask',
        type: 'ask',
        label: '확인 질문',
        question: [text('어느 지역 기준인가요?')],
        timeout_minutes: 60,
      },
      {
        id: 'ping',
        type: 'notify',
        label: '알림',
        title: [text('지역: '), ref('ask', '/answer')],
      },
      { id: 'now', type: 'tool', label: '현재 시각', tool: 'current_datetime' },
      {
        id: 'done',
        type: 'output',
        label: '출력',
        fields: {
          count: [ref('each', '/count')],
          region: [ref('ask', '/answer')],
          reason: [ref('enough', '/reason')],
        },
      },
      { id: 'stop', type: 'fail', label: '중단', reason: '중단했습니다' },
    ],
    edges: [
      { from: 'start', to: 'lister' },
      { from: 'lister', to: 'each' },
      { from: 'each', to: 'views' },
      { from: 'views', to: 'sum' },
      { from: 'sum', to: 'enough' },
      { from: 'enough', to: 'route', when: 'true' },
      { from: 'enough', to: 'stop', when: 'false' },
      { from: 'route', to: 'ask', when: 'news' },
      { from: 'route', to: 'stop', when: 'default' },
      { from: 'ask', to: 'ping', when: 'answered' },
      { from: 'ask', to: 'stop', when: 'timeout' },
      { from: 'ping', to: 'now' },
      { from: 'now', to: 'done' },
    ],
  }
}

test.describe('workflow nodes — every remaining kind', () => {
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
      name: `E2E 조사원 ${stamp}`,
      system_prompt: '주제를 조사합니다.',
      model_id: scripted.id,
    })
    const created = await send<{ id: string; draft_revision: number }>(
      request,
      csrf,
      '/api/workflows',
      { name: `E2E 모든 노드 ${stamp}` },
    )
    workflowId = created.id
    const saved = await send<{ draft_revision: number }>(
      request,
      csrf,
      `/api/workflows/${workflowId}/draft`,
      { revision: created.draft_revision, definition: everyNode(agent.id) },
      'put',
    )
    await send(request, csrf, `/api/workflows/${workflowId}/publish`, {
      revision: saved.draft_revision,
    })
  })

  test('the editor shows the new kinds and their settings', async ({ page }) => {
    await page.goto(`/workflows/${workflowId}`)
    await expect(page.getByTestId('workflow-node-each')).toContainText('주제마다 조사', SLOW)
    await expect(page.getByTestId('workflow-node-route')).toContainText('news')
    await page.getByTestId('workflow-node-each').click()
    await expect(page.getByRole('combobox', { name: '목록 값' })).toBeVisible()
    await page.getByTestId('workflow-node-views').click()
    await expect(page.getByRole('region', { name: '갈래 pro' })).toBeVisible()
    await page.getByTestId('workflow-node-enough').click()
    await expect(page.getByRole('radio', { name: 'LLM' })).toHaveAttribute('aria-checked', 'true')
  })

  test('a run goes through every kind, waits for the answer and completes', async ({
    page,
    request,
  }) => {
    const csrf = await login(request)
    const run = await send<Run>(request, csrf, `/api/workflows/${workflowId}/runs`, {
      inputs: { topic: '뉴스' },
    })
    await page.goto(`/workflows/${workflowId}/runs/${run.id}`)
    // The ask step: answered right on the run page.
    await expect(page.getByText('어느 지역 기준인가요?')).toBeVisible(SLOW)
    await page.getByRole('textbox', { name: '답' }).fill('서울')
    await page.getByRole('button', { name: '답 보내기' }).click()
    await expect(page.getByText('완료', { exact: true }).first()).toBeVisible(SLOW)
    await expect(page.getByRole('list', { name: '하위 실행' }).first()).toBeVisible()

    let detail: Run | null = null
    await expect
      .poll(async () => {
        detail = (await (await request.get(`${API}/api/workflow-runs/${run.id}`)).json()) as Run
        return detail.status
      }, SLOW)
      .toBe('completed')
    const done = detail as unknown as Run
    expect(done.output).toMatchObject({ count: 2, region: '서울', reason: 'E2E 응답' })
    const main = done.steps.filter((step) => !step.parent_step_id)
    expect(main.map((step) => step.node_id)).toEqual([
      'start',
      'lister',
      'each',
      'views',
      'sum',
      'enough',
      'route',
      'ask',
      'ping',
      'now',
      'done',
    ])
    expect(main.find((step) => step.node_id === 'route')?.branch).toBe('news')
    expect(done.steps.filter((step) => step.parent_step_id).map((s) => s.node_id)).toEqual([
      'each.0',
      'each.1',
      'views.pro',
      'views.con',
    ])
    const notices = (await (await request.get(`${API}/api/notifications`)).json()) as {
      items: { kind: string; payload: Record<string, unknown> }[]
    }
    expect(
      notices.items.some((n) => n.kind === 'workflow.notify' && n.payload.title === '지역: 서울'),
    ).toBe(true)
  })
})
