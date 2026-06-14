import fs from 'node:fs/promises'
import path from 'node:path'
import type { APIRequestContext, Page } from '@playwright/test'
import {
  API_BASE,
  apiDeleteOk,
  apiGetJson,
  apiPostJson,
  expect,
  isRecord,
  loginApi,
  test,
  type CsrfHeaders,
} from './fixtures'
import {
  records,
  sendMessage,
  setupLangGraphV3Agent,
  waitForActiveRun,
  waitForRunStatus,
} from './langgraph-v3-helpers'

const CAPTURE_DIR = path.join(
  '..',
  'output',
  'e2e-captures',
  '20260614-chat-surfaces-live',
)
const DESKTOP_VIEWPORT = { width: 1366, height: 900 } as const

async function capture(page: Page, filename: string): Promise<void> {
  await page.screenshot({ path: path.join(CAPTURE_DIR, filename), fullPage: true })
}

async function scriptedModelId(request: APIRequestContext): Promise<string> {
  const models = records(await apiGetJson(request, `${API_BASE}/api/models`), 'models')
  const model = models.find(
    (row) => row.provider === 'e2e_scripted' && row.model_name === 'document-artifact-scripted',
  )
  if (!model) throw new Error('E2E scripted model is not seeded')
  const id = model.id
  if (typeof id !== 'string') throw new Error('E2E scripted model id is invalid')
  return id
}

async function createSimpleAgent(
  request: APIRequestContext,
  csrfHeaders: CsrfHeaders,
  name: string,
): Promise<string> {
  const agent = await apiPostJson(request, `${API_BASE}/api/agents`, csrfHeaders, {
    name,
    system_prompt: 'You are a deterministic E2E chat surface fixture agent.',
    model_id: await scriptedModelId(request),
  })
  if (!isRecord(agent) || typeof agent.id !== 'string') {
    throw new Error('agent create did not return an id')
  }
  return agent.id
}

async function createConversation(
  request: APIRequestContext,
  csrfHeaders: CsrfHeaders,
  agentId: string,
): Promise<string> {
  const conversation = await apiPostJson(
    request,
    `${API_BASE}/api/agents/${agentId}/conversations`,
    csrfHeaders,
    { title: 'Live chat surface capture' },
  )
  if (!isRecord(conversation) || typeof conversation.id !== 'string') {
    throw new Error('conversation create did not return an id')
  }
  return conversation.id
}

async function assistantMessageCount(
  request: APIRequestContext,
  conversationId: string,
): Promise<number> {
  const envelope = await apiGetJson(
    request,
    `${API_BASE}/api/conversations/${conversationId}/messages`,
  )
  if (!isRecord(envelope) || !Array.isArray(envelope.messages)) return 0
  return envelope.messages.filter(
    (message) =>
      isRecord(message) && message.role === 'assistant' && typeof message.content === 'string',
  ).length
}

async function waitForBodySignal(page: Page, pattern: RegExp, timeout: number): Promise<boolean> {
  try {
    await page.waitForFunction(
      (source) => new RegExp(source, 'i').test(document.body.innerText),
      pattern.source,
      { timeout },
    )
    return true
  } catch {
    return false
  }
}

async function waitForStreamToSettle(page: Page, timeout = 90_000): Promise<void> {
  const stopButton = page.locator('[data-moldy-stop-button="true"]:visible').last()
  await stopButton.waitFor({ state: 'visible', timeout: 10_000 }).catch(() => {})
  await stopButton.waitFor({ state: 'hidden', timeout }).catch(() => {})
}

async function fillAndSendComposer(page: Page, text: string): Promise<void> {
  const composer = page.locator('textarea[data-moldy-composer-input="true"]:visible').last()
  await expect(composer).toBeVisible({ timeout: 20_000 })
  await composer.fill(text)
  await composer.press('Enter')
}

test.describe('Live chat surface captures', () => {
  test.skip(process.env.PW_SKIP_BACKEND === '1', 'Requires the FastAPI backend')
  test.skip(
    process.env.E2E_LIVE_CHAT_SURFACES !== '1',
    'Set E2E_LIVE_CHAT_SURFACES=1 to capture live chat surfaces',
  )

  test.beforeEach(async ({ page }) => {
    await fs.mkdir(CAPTURE_DIR, { recursive: true })
    await page.setViewportSize(DESKTOP_VIEWPORT)
  })

  test('captures main agent chat branch picker after regenerate', async ({ page, request }) => {
    test.setTimeout(120_000)
    const csrfHeaders = await loginApi(request)
    const agentId = await createSimpleAgent(
      request,
      csrfHeaders,
      `E2E Surface Branch ${Date.now()}`,
    )

    try {
      const conversationId = await createConversation(request, csrfHeaders, agentId)
      await page.goto(`/agents/${agentId}/conversations/${conversationId}`)
      await sendMessage(page, 'First question for regenerate')
      await expect
        .poll(() => assistantMessageCount(request, conversationId), {
          timeout: 60_000,
          intervals: [500, 1000],
        })
        .toBeGreaterThan(0)

      await page.getByRole('button', { name: '재생성' }).first().click()
      await expect(page.getByRole('button', { name: '이전 분기' })).toBeVisible({
        timeout: 60_000,
      })
      await expect(page.getByText('2/2').first()).toBeVisible()
      await capture(page, '01-main-chat-branch-picker.png')
    } finally {
      await apiDeleteOk(request, `${API_BASE}/api/agents/${agentId}`, csrfHeaders)
    }
  })

  test('captures LangGraph v3 planning, subagent, and HITL state', async ({ page, request }) => {
    test.setTimeout(180_000)
    const setup = await setupLangGraphV3Agent(request)

    try {
      await page.goto(`/agents/${setup.parentAgentId}/conversations/${setup.conversationId}`)
      await sendMessage(
        page,
        `E2E_LANGGRAPH_V3 slow_subagent=true subagent=${setup.childRuntimeName}`,
      )
      const runId = await waitForActiveRun(request, setup.conversationId)
      await expect(page.getByText('Collect LangGraph v3 runtime evidence')).toBeVisible({
        timeout: 30_000,
      })
      await waitForRunStatus(request, setup.conversationId, runId, 'interrupted')
      await expect(page.getByText(/승인이 필요합니다|Approval Required/).last()).toBeVisible({
        timeout: 30_000,
      })
      await capture(page, '02-main-chat-v3-planning-hitl.png')
    } finally {
      await apiDeleteOk(request, `${API_BASE}/api/agents/${setup.parentAgentId}`, setup.csrfHeaders)
      await apiDeleteOk(request, `${API_BASE}/api/agents/${setup.childAgentId}`, setup.csrfHeaders)
    }
  })

  test('captures conversational builder with live System LLM state', async ({ page }) => {
    test.setTimeout(120_000)
    const prompt = 'Create an agent named SurfaceBot that summarizes product feedback.'
    await page.goto(`/agents/new/conversational?initialMessage=${encodeURIComponent(prompt)}`)
    await expect(page.getByText(/세션 #/)).toBeVisible({ timeout: 30_000 })
    await expect(page.getByText(prompt).first()).toBeVisible()

    await waitForBodySignal(
      page,
      /진행 상황|프로젝트 초기화|오류|error|quota|rate|한도|System LLM/,
      75_000,
    )
    await waitForStreamToSettle(page)
    await capture(page, '03-builder-conversational-live.png')
  })

  test('captures settings Fix assistant panel after a live message', async ({ page, request }) => {
    test.setTimeout(120_000)
    const csrfHeaders = await loginApi(request)
    const agentId = await createSimpleAgent(
      request,
      csrfHeaders,
      `E2E Surface Fix ${Date.now()}`,
    )

    try {
      await page.goto(`/agents/${agentId}/settings`)
      const prompt = '현재 에이전트 설정을 읽고 개선할 점을 한 문장으로 알려줘.'
      await fillAndSendComposer(page, prompt)
      await expect(page.getByText(prompt).first()).toBeVisible()

      await waitForBodySignal(
        page,
        /운영자가 System LLM|오류|error|quota|rate|한도|개선|설정|모델|프롬프트/,
        75_000,
      )
      await waitForStreamToSettle(page)
      await capture(page, '04-settings-fix-assistant-live.png')
    } finally {
      await apiDeleteOk(request, `${API_BASE}/api/agents/${agentId}`, csrfHeaders)
    }
  })
})
