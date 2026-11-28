import { randomUUID } from 'node:crypto'
import { z } from 'zod'

import {
  independentJobListSchema,
  independentJobSchema,
} from '../src/lib/types/independent-chat-job'
import { API_BASE, apiGetJson, apiPostJson, expect, loginApi, test } from './fixtures'
import { startIndependentJobModel } from './helpers/independent-job-model'
import { isSeededScriptedModel } from './helpers/scripted-model'
import { records, sendMessageForRun, waitForRunStatus } from './langgraph-v3-helpers'

const resourceSchema = z.object({ id: z.uuid() })
const noticePageSchema = z.object({
  items: z.array(z.object({ id: z.uuid(), kind: z.string(), link: z.string().nullable() })),
})

test('restores one independent result and its completion notice after a duplicate submission and reconnect', async ({
  page,
  request,
}) => {
  test.setTimeout(150_000)
  const csrf = await loginApi(request)
  const create = async (path: string, data: Record<string, unknown>) =>
    resourceSchema.parse(await apiPostJson(request, API_BASE + path, csrf, data)).id
  const get = (path: string) => apiGetJson(request, API_BASE + path)
  const notices = async () =>
    noticePageSchema
      .parse(await get('/api/notifications?limit=50'))
      .items.filter((item) => item.kind === 'chat_task.completed')

  const models = records(await get('/api/models'), 'models')
  const parentModel = models.find(isSeededScriptedModel)
  if (!parentModel) throw new Error('Seeded scripted model is missing')

  const id = randomUUID().slice(0, 8)
  const reply = `독립 작업 ${id}의 결과를 확인했습니다.`
  const model = await startIndependentJobModel(reply)
  // The dedicated lane destroys its database, including these credentials and run receipts.
  try {
    const credentialId = await create('/api/credentials', {
      name: `Independent model ${id}`,
      definition_key: 'openai_compatible',
      data: { api_key: model.apiKey, base_url: model.baseURL },
    })
    const modelId = await create('/api/models', {
      provider: 'openai_compatible',
      model_name: `independent-model-${id}`,
      display_name: `Independent model ${id}`,
      base_url: model.baseURL,
      source: 'manual',
      supports_function_calling: true,
      context_window: 32000,
      default_credential_id: credentialId,
    })
    const childId = await create('/api/agents', {
      name: `독립 조사 ${id}`,
      system_prompt: '요청을 한국어로 한 문장에 마무리한다.',
      model_id: modelId,
      runtime_policy: { version: 1 },
    })
    const memory = await request.patch(`${API_BASE}/api/agents/${childId}/memory-settings`, {
      headers: csrf,
      data: { memory_policy_override: 'off' },
    })
    expect(memory.ok()).toBe(true)
    expect(await memory.json()).toMatchObject({ memory_policy_override: 'off' })
    const parentId = await create('/api/agents', {
      name: `별도 작업 검수 ${id}`,
      system_prompt: '짧게 답한다.',
      model_id: parentModel.id,
      sub_agent_ids: [childId],
      runtime_policy: { version: 1 },
    })
    const conversationId = await create(`/api/agents/${parentId}/conversations`, {
      title: `재접속 검수 ${id}`,
    })
    const parentPath = `/agents/${parentId}/conversations/${conversationId}`
    const previousNotices = new Set((await notices()).map((item) => item.id))

    await page.goto(parentPath)
    const runId = await sendMessageForRun(
      page,
      conversationId,
      'E2E_SLOW_STREAM 부모 대화를 마칩니다.',
    )
    await waitForRunStatus(request, conversationId, runId, 'completed')
    const prefix = `/api/conversations/${conversationId}`
    const eligible = independentJobListSchema.parse(
      await get(`${prefix}/independent-jobs?run_id=${runId}`),
    )
    expect(eligible.targets.map((target) => target.id)).toEqual([childId])
    await page.getByRole('button', { name: '별도 작업으로 계속' }).click()
    const dialog = page.getByRole('dialog')
    const instruction = '조사 결과를 한 문장으로 정리해 주세요.'
    await dialog.getByRole('textbox', { name: '맡길 일' }).fill(instruction)
    const url = `${API_BASE}${prefix}/runs/${runId}/independent-jobs`
    const [response] = await Promise.all([
      page.waitForResponse(
        (response) => response.url() === url && response.request().method() === 'POST',
      ),
      dialog.getByRole('button', { name: '작업 시작' }).click(),
    ])
    expect(response.status()).toBe(201)
    const job = independentJobSchema.parse(await response.json())
    const submitted = z
      .object({ request_id: z.uuid(), target_id: z.uuid(), instruction: z.string() })
      .parse(response.request().postDataJSON())
    expect(submitted).toMatchObject({ target_id: childId, instruction })
    const replay = await request.post(url, { headers: csrf, data: submitted })
    expect(replay.status()).toBe(200)
    expect(await replay.json()).toMatchObject({
      id: job.id,
      child_conversation_id: job.child_conversation_id,
      child_run_id: job.child_run_id,
    })

    await page.reload()
    const resultRegion = page.getByRole('region', { name: '별도 작업' })
    const result = resultRegion.getByRole('link', { name: '결과 열기' })
    const childPath = `/agents/${childId}/conversations/${job.child_conversation_id}`
    await expect(result).toHaveAttribute('href', childPath, { timeout: 60_000 })
    await expect(resultRegion.getByRole('listitem')).toHaveCount(1)
    await result.click()
    await expect(page.getByText(reply, { exact: true })).toBeVisible()
    await expect(page.locator('textarea[data-moldy-composer-input="true"]')).toHaveCount(0)
    const back = page.getByRole('link', { name: '원래 대화 열기' })
    await expect(back).toHaveAttribute('href', parentPath)
    await back.click()
    await expect(result).toBeVisible()

    const childPrefix = `/api/conversations/${job.child_conversation_id}`
    await waitForRunStatus(request, job.child_conversation_id, job.child_run_id, 'completed')
    const history = z
      .object({
        conversation_source: z.literal('independent_job'),
        messages: z.array(z.object({ id: z.uuid(), role: z.string(), content: z.string() })),
      })
      .parse(await get(`${childPrefix}/messages`))
    const assistants = history.messages.filter((message) => message.role === 'assistant')
    expect(assistants.map((message) => message.content)).toEqual([reply])
    expect(await get(`${childPrefix}/run-message-links?message_id=${assistants[0].id}`)).toEqual([
      { message_id: assistants[0].id, run_id: job.child_run_id },
    ])
    expect(model.getCompletionCount()).toBe(1)

    let completionNoticeId: string | undefined
    await expect
      .poll(
        async () => {
          const matches = []
          for (const notice of (await notices()).filter((item) => !previousNotices.has(item.id))) {
            const destination = z
              .object({
                job_id: z.uuid(),
                conversation_id: z.uuid(),
                run_id: z.uuid(),
                agent_id: z.uuid(),
              })
              .parse(await get(`/api/notifications/${notice.id}/destination`))
            if (destination.job_id === job.id) {
              matches.push({ ...destination, link: notice.link })
              completionNoticeId = notice.id
            }
          }
          return matches
        },
        { timeout: 35_000 },
      )
      .toEqual([
        {
          job_id: job.id,
          conversation_id: job.child_conversation_id,
          run_id: job.child_run_id,
          agent_id: childId,
          link: childPath,
        },
      ])
    await page.getByTestId('notifications-bell').click()
    await page.locator(`[data-notification-id="${completionNoticeId}"]`).click()
    await expect(page).toHaveURL(new RegExp(`${childPath}$`))
    await expect(page.getByText(reply, { exact: true })).toBeVisible()
  } finally {
    await model.close()
  }
})
