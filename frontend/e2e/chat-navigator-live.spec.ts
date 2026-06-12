import { test, expect } from './fixtures'
import type { APIRequestContext, APIResponse, Page } from '@playwright/test'
import { mkdir } from 'node:fs/promises'
import { join } from 'node:path'

const BACKEND_PORT = process.env.E2E_BACKEND_PORT ?? '8001'
const API_BASE = process.env.E2E_API_BASE_URL ?? `http://localhost:${BACKEND_PORT}`
const E2E_EMAIL = process.env.E2E_USER_EMAIL ?? process.env.E2E_EMAIL ?? 'playwright-e2e@moldy.dev'
const E2E_PASSWORD =
  process.env.E2E_USER_PASSWORD ?? process.env.E2E_PASSWORD ?? 'correct horse battery staple 42'
const CAPTURE_DIR = join(
  process.cwd(),
  '..',
  'output',
  'e2e-captures',
  '20260611-chat-navigator-live',
)

type JsonObject = Record<string, unknown>

interface CreatedAgent {
  readonly id: string
  readonly name: string
}

interface CreatedConversation {
  readonly id: string
  readonly title: string
}

function isJsonObject(value: unknown): value is JsonObject {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
}

async function expectOk(response: APIResponse, label: string): Promise<void> {
  if (response.ok()) return
  const body = await response.text()
  throw new TypeError(`${label} failed (${response.status()}): ${body.slice(0, 500)}`)
}

async function readObject(response: APIResponse, label: string): Promise<JsonObject> {
  const value: unknown = await response.json()
  if (!isJsonObject(value)) {
    throw new TypeError(`${label} response was not a JSON object`)
  }
  return value
}

async function readObjects(response: APIResponse, label: string): Promise<readonly JsonObject[]> {
  const value: unknown = await response.json()
  if (!Array.isArray(value) || !value.every(isJsonObject)) {
    throw new TypeError(`${label} response was not a JSON object array`)
  }
  return value
}

function stringField(object: JsonObject, key: string, label: string): string {
  const value = object[key]
  if (typeof value !== 'string') {
    throw new TypeError(`${label}.${key} was not a string`)
  }
  return value
}

async function loginApi(request: APIRequestContext): Promise<Record<string, string>> {
  const response = await request.post(`${API_BASE}/api/auth/login`, {
    data: { email: E2E_EMAIL, password: E2E_PASSWORD },
  })
  await expectOk(response, 'login')
  const body = await readObject(response, 'login')
  return { 'X-CSRF-Token': stringField(body, 'csrf_token', 'login') }
}

async function firstModelId(request: APIRequestContext): Promise<string> {
  const response = await request.get(`${API_BASE}/api/models`)
  await expectOk(response, 'models')
  const models = await readObjects(response, 'models')
  expect(models.length).toBeGreaterThan(0)
  const firstModel = models[0]
  if (!firstModel) {
    throw new TypeError('models[0] was missing')
  }
  return stringField(firstModel, 'id', 'models[0]')
}

async function createAgent(
  request: APIRequestContext,
  headers: Record<string, string>,
  modelId: string,
  name: string,
): Promise<CreatedAgent> {
  const response = await request.post(`${API_BASE}/api/agents`, {
    headers,
    data: {
      name,
      description: `${name} live sidebar fixture`,
      system_prompt: 'You are a chat navigator E2E fixture agent.',
      model_id: modelId,
    },
  })
  await expectOk(response, 'create agent')
  const body = await readObject(response, 'agent')
  return { id: stringField(body, 'id', 'agent'), name: stringField(body, 'name', 'agent') }
}

async function createConversation(
  request: APIRequestContext,
  headers: Record<string, string>,
  agentId: string,
  title: string,
): Promise<CreatedConversation> {
  const response = await request.post(`${API_BASE}/api/agents/${agentId}/conversations`, {
    headers,
    data: { title },
  })
  await expectOk(response, 'create conversation')
  const body = await readObject(response, 'conversation')
  return {
    id: stringField(body, 'id', 'conversation'),
    title: stringField(body, 'title', 'conversation'),
  }
}

async function deleteAgent(
  request: APIRequestContext,
  headers: Record<string, string>,
  agentId: string,
): Promise<void> {
  const response = await request.delete(`${API_BASE}/api/agents/${agentId}`, { headers })
  await expectOk(response, 'delete agent')
}

async function capture(page: Page, name: string): Promise<void> {
  await mkdir(CAPTURE_DIR, { recursive: true })
  await page.screenshot({ path: join(CAPTURE_DIR, name), fullPage: true })
}

function requireFixture<T>(value: T | null, label: string): T {
  if (value === null) {
    throw new TypeError(`${label} was not initialized`)
  }
  return value
}

test.describe('Chat navigator live integration', () => {
  test.skip(process.env.PW_SKIP_BACKEND === '1', 'Requires the FastAPI backend')

  let csrfHeaders: Record<string, string> | null = null
  let alphaAgent: CreatedAgent | null = null
  let betaAgent: CreatedAgent | null = null
  let alphaConversation: CreatedConversation | null = null

  test.beforeAll(async ({ request }) => {
    csrfHeaders = await loginApi(request)
    const modelId = await firstModelId(request)
    const headers = csrfHeaders
    alphaAgent = await createAgent(request, headers, modelId, 'E2E Navigator Alpha')
    betaAgent = await createAgent(request, headers, modelId, 'E2E Navigator Beta')
    alphaConversation = await createConversation(
      request,
      headers,
      alphaAgent.id,
      'Navigator Alpha kickoff',
    )
    await createConversation(request, headers, alphaAgent.id, 'Navigator Alpha second session')
    await createConversation(request, headers, betaAgent.id, 'Navigator Beta roadmap')
  })

  test.afterAll(async ({ request }) => {
    if (!alphaAgent?.id && !betaAgent?.id) return
    const cleanupHeaders = await loginApi(request)
    if (alphaAgent?.id) await deleteAgent(request, cleanupHeaders, alphaAgent.id)
    if (betaAgent?.id) await deleteAgent(request, cleanupHeaders, betaAgent.id)
  })

  test('renders consolidated sidebar from the live API and searches across agents', async ({
    page,
    errors,
  }) => {
    const alpha = requireFixture(alphaAgent, 'alphaAgent')
    const conversation = requireFixture(alphaConversation, 'alphaConversation')

    await page.goto(`/agents/${alpha.id}/conversations/${conversation.id}`)
    await page.waitForLoadState('domcontentloaded')

    await expect(page.getByRole('textbox', { name: '에이전트 또는 대화 검색' })).toHaveCount(0)
    await expect(page.getByText(alpha.name).first()).toBeVisible()
    await expect(page.getByText(conversation.title).first()).toBeVisible()

    const activeRow = page.locator(
      `[data-chat-session-href="/agents/${alpha.id}/conversations/${conversation.id}"]`,
    )
    await activeRow.hover()
    await activeRow.getByRole('button', { name: '대화 메뉴' }).click()
    await expect(page.getByRole('menuitem', { name: /이름 변경/ })).toBeVisible()
    await expect(page.getByRole('menuitem', { name: /공유/ })).toBeVisible()
    await page.keyboard.press('Escape')

    await page.getByRole('button', { name: '에이전트 검색' }).click()
    await page.getByRole('textbox', { name: '에이전트 또는 대화 검색' }).fill('Beta roadmap')
    await expect(page.getByText('검색 결과')).toBeVisible()
    await expect(page.getByText('Navigator Beta roadmap').first()).toBeVisible()
    await expect(page.getByText('검색 결과가 없습니다')).toHaveCount(0)
    await capture(page, 'chat-navigator-live-search.png')

    await page.getByRole('button', { name: '새 채팅' }).click()
    await expect(page).toHaveURL(new RegExp(`/agents/${alpha.id}/conversations/new$`))

    expect(errors.console).toEqual([])
    expect(errors.network).toEqual([])
  })
})
