import { test, expect } from './fixtures'
import type { APIRequestContext } from '@playwright/test'

import { isSeededScriptedModel } from './helpers/scripted-model'

// workflow-tool-plan T-d: an agent calls a published workflow as a tool in chat, end to end on
// the keyless scripted model and the job worker. "E2E_CALL_WORKFLOW" makes the chat model call
// the workflow tool; "E2E_WORKFLOW_YES" makes the workflow's classifier mark it urgent, so it
// waits on the runner's approval — decided right in the chat card — and the chat then resumes
// on its own (the leader's resume sweep) with the workflow's result.
const API =
  process.env.E2E_API_BASE_URL ?? `http://localhost:${process.env.E2E_BACKEND_PORT ?? '8001'}`
const EMAIL = process.env.E2E_USER_EMAIL ?? process.env.E2E_EMAIL ?? 'playwright-e2e@moldy.dev'
const PASSWORD =
  process.env.E2E_USER_PASSWORD ?? process.env.E2E_PASSWORD ?? 'correct horse battery staple 42'
const SLOW = { timeout: 45_000 }

type Run = { id: string; status: string; parent_conversation_id: string | null }

async function login(request: APIRequestContext): Promise<Record<string, string>> {
  const res = await request.post(`${API}/api/auth/login`, {
    data: { email: EMAIL, password: PASSWORD },
  })
  expect(res.ok()).toBeTruthy()
  return { 'X-CSRF-Token': (await res.json()).csrf_token as string }
}

async function post<T>(
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

const ref = (node: string, path: string) => ({ ref: { node, path } })
const text = (value: string) => ({ text: value })

function inquiryWorkflow(agentId: string) {
  return {
    schema_version: 'moldy.workflow.v1',
    inputs: [{ name: 'inquiry', type: 'string', required: true, label: '문의 내용' }],
    nodes: [
      { id: 'start', type: 'start' },
      {
        id: 'classify',
        type: 'agent',
        label: '문의 분류',
        agent_id: agentId,
        prompt: [text('분류: '), ref('input', '/inquiry')],
        output_schema: {
          type: 'object',
          properties: { urgent: { type: 'boolean' }, summary: { type: 'string' } },
          required: ['urgent', 'summary'],
        },
      },
      {
        id: 'is_urgent',
        type: 'decision_bool',
        label: '긴급한가',
        condition: { all: [{ source: 'classify', path: '/urgent', op: 'eq', value: true }] },
      },
      {
        id: 'review',
        type: 'approval',
        label: '담당자 승인',
        title: [text('긴급 문의: '), ref('classify', '/summary')],
        timeout_minutes: 60,
      },
      {
        id: 'done',
        type: 'output',
        label: '출력',
        fields: { summary: [ref('classify', '/summary')] },
      },
      { id: 'stop', type: 'fail', label: '거절 종료', reason: '거절했습니다' },
    ],
    edges: [
      { from: 'start', to: 'classify' },
      { from: 'classify', to: 'is_urgent' },
      { from: 'is_urgent', to: 'review', when: 'true' },
      { from: 'is_urgent', to: 'done', when: 'false' },
      { from: 'review', to: 'done', when: 'approved' },
      { from: 'review', to: 'stop', when: 'rejected' },
    ],
  }
}

test.describe('workflow as a chat tool', () => {
  test.skip(process.env.PW_SKIP_BACKEND === '1', 'Requires the FastAPI backend')
  test.setTimeout(180_000)

  let chatAgentId = ''
  let workflowId = ''

  test.beforeAll(async ({ request }) => {
    const csrf = await login(request)
    const models = (await (await request.get(`${API}/api/models`)).json()) as unknown[]
    const scripted = models.find(isSeededScriptedModel)
    if (!scripted) throw new Error('the seeded scripted model should exist')
    const stamp = Date.now()
    const agent = (name: string) =>
      post<{ id: string }>(request, csrf, '/api/agents', {
        name,
        system_prompt: '고객 문의를 처리합니다.',
        model_id: scripted.id,
      })
    const classifier = await agent(`E2E 분류 ${stamp}`)
    const chat = await agent(`E2E 상담 ${stamp}`)
    chatAgentId = chat.id
    const created = await post<{ id: string; draft_revision: number }>(
      request,
      csrf,
      '/api/workflows',
      { name: `E2E 문의 처리 ${stamp}` },
    )
    workflowId = created.id
    const saved = await post<{ draft_revision: number }>(
      request,
      csrf,
      `/api/workflows/${workflowId}/draft`,
      { revision: created.draft_revision, definition: inquiryWorkflow(classifier.id) },
      'put',
    )
    await post(request, csrf, `/api/workflows/${workflowId}/publish`, {
      revision: saved.draft_revision,
    })
  })

  test('the agent calls the workflow, its approval is decided in the chat, the chat resumes', async ({
    page,
    request,
  }) => {
    // Link it on the agent's settings, as a person would.
    await page.goto(`/agents/${chatAgentId}/settings`)
    const panel = page.getByRole('region', { name: '채팅에서 부를 워크플로' })
    await panel.getByLabel('붙일 워크플로').selectOption(workflowId)
    await panel.getByRole('button', { name: '붙이기' }).click()
    await expect(panel.getByRole('list', { name: '붙인 워크플로' })).toContainText(
      'E2E 문의 처리',
      SLOW,
    )

    const csrf = await login(request)
    const conversation = await post<{ id: string }>(
      request,
      csrf,
      `/api/agents/${chatAgentId}/conversations`,
      { title: 'E2E 워크플로 도구' },
    )
    await page.goto(`/agents/${chatAgentId}/conversations/${conversation.id}`)
    const composer = page.locator('textarea[data-moldy-composer-input="true"]').last()
    await expect(composer).toBeVisible(SLOW)
    await composer.fill('E2E_CALL_WORKFLOW E2E_WORKFLOW_YES 결제가 두 번 됐어요')
    await composer.press('Enter')

    // The workflow card: waiting on the approval, decided right here.
    const card = page.getByRole('region', { name: /^워크플로 E2E 문의 처리/ })
    await expect(card).toContainText('승인 대기', SLOW)
    await card.getByRole('button', { name: '승인 후 계속' }).click()
    await expect(card).toContainText('완료', SLOW)

    // The chat resumed with the result by itself.
    await expect(
      page.locator('[data-moldy-message-id]').filter({ hasText: '워크플로 결과를 받았어요' }),
    ).toBeVisible(SLOW)
    const runs = (await (
      await request.get(`${API}/api/workflows/${workflowId}/runs`)
    ).json()) as Run[]
    expect(runs[0]).toMatchObject({ status: 'completed', parent_conversation_id: conversation.id })
  })
})
