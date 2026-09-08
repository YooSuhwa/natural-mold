import type { APIRequestContext } from '@playwright/test'

import {
  API_BASE,
  apiDeleteOk,
  apiGetJson,
  apiPostJson,
  expect,
  isRecord,
  loginApi,
  test,
} from './fixtures'
import { records, sendMessageForRun, stringField, waitForRunStatus } from './langgraph-v3-helpers'

const QUALITY_MARKER = 'MOLDY_LIVE_OK'
const REQUIRED_WORDS = ['연결', '실행', '응답'] as const

async function liveModelId(request: APIRequestContext): Promise<string> {
  const models = records(await apiGetJson(request, `${API_BASE}/api/models`), 'models')
  const model = models.find((row) => row.provider === 'openai_compatible')
  if (!model) throw new Error('The live E2E model was not seeded')
  return stringField(model, 'id', 'live model')
}

async function persistedAssistantContent(
  request: APIRequestContext,
  conversationId: string,
): Promise<string> {
  const envelope = await apiGetJson(
    request,
    `${API_BASE}/api/conversations/${conversationId}/messages`,
  )
  if (!isRecord(envelope) || !Array.isArray(envelope.messages)) return ''
  const assistantMessages = envelope.messages.filter(
    (message) => isRecord(message) && message.role === 'assistant',
  )
  const latest = assistantMessages.at(-1)
  return latest && typeof latest.content === 'string' ? latest.content : ''
}

test('follows a bounded instruction through a live model chat', async ({
  page,
  request,
  errors,
}) => {
  test.setTimeout(150_000)
  const csrfHeaders = await loginApi(request)
  const agent = await apiPostJson(request, `${API_BASE}/api/agents`, csrfHeaders, {
    name: `E2E Live Quality ${Date.now()}`,
    description: 'Live provider instruction-following quality gate.',
    system_prompt:
      'Answer in one short Korean sentence. Include the exact marker MOLDY_LIVE_OK and the words 연결, 실행, 응답. Do not use tools, markdown, or extra commentary.',
    model_id: await liveModelId(request),
    tool_ids: [],
    mcp_tool_ids: [],
    skill_ids: [],
    sub_agent_ids: [],
    middleware_configs: [],
  })
  if (!isRecord(agent)) throw new Error('Create live quality agent did not return an object')
  const agentId = stringField(agent, 'id', 'live quality agent')

  try {
    const conversation = await apiPostJson(
      request,
      `${API_BASE}/api/agents/${agentId}/conversations`,
      csrfHeaders,
      { title: 'Live instruction quality gate' },
    )
    if (!isRecord(conversation)) {
      throw new Error('Create live quality conversation did not return an object')
    }
    const conversationId = stringField(conversation, 'id', 'live quality conversation')

    await page.goto(`/agents/${agentId}/conversations/${conversationId}`)
    const runId = await sendMessageForRun(
      page,
      conversationId,
      '연결 상태를 확인하고 요청한 형식으로만 답해 주세요.',
    )
    await waitForRunStatus(request, conversationId, runId, 'completed', 90_000)

    let content = ''
    await expect
      .poll(
        async () => {
          content = await persistedAssistantContent(request, conversationId)
          return content.includes(QUALITY_MARKER)
        },
        { timeout: 30_000, intervals: [500, 1000, 2000] },
      )
      .toBe(true)
    for (const word of REQUIRED_WORDS) expect(content).toContain(word)

    const assistantMessage = page
      .locator('[data-moldy-message-role="assistant"]')
      .filter({ hasText: QUALITY_MARKER })
      .last()
    if (!(await assistantMessage.isVisible())) await page.reload()
    await expect(assistantMessage).toBeVisible({ timeout: 30_000 })
    for (const word of REQUIRED_WORDS) await expect(assistantMessage).toContainText(word)
    expect(errors.console).toEqual([])
    expect(errors.network).toEqual([])
  } finally {
    await apiDeleteOk(request, `${API_BASE}/api/agents/${agentId}`, csrfHeaders)
  }
})
