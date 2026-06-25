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
    // This test navigates the heavy conversation route first; a cold Next dev
    // app compile can exceed the default 60s budget, matching the siblings below.
    test.setTimeout(120_000)
    const alpha = requireFixture(alphaAgent, 'alphaAgent')
    const conversation = requireFixture(alphaConversation, 'alphaConversation')

    // waitUntil 'domcontentloaded' (not the default 'load'): the heavy chat route
    // keeps streaming/polling so 'load' can lag past the budget and the frame
    // detaches (ERR_ABORTED) on a cold first navigation.
    await page.goto(`/agents/${alpha.id}/conversations/${conversation.id}`, {
      waitUntil: 'domcontentloaded',
      timeout: 120_000,
    })

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

  test('row menu pins, renames, and deletes conversations end to end', async ({
    page,
    request,
    errors,
  }) => {
    // Multi-step menu flow (pin → rename → delete) plus a cold first-navigation
    // route compile exceeds the default 60s budget when run in isolation.
    test.setTimeout(120_000)
    const headers = await loginApi(request)
    const modelId = await firstModelId(request)
    const agent = await createAgent(request, headers, modelId, `E2E Navigator Mgmt ${Date.now()}`)

    try {
      // `target` is the active conversation we pin + rename; `keep` is a second,
      // non-active row we delete (deleting the ACTIVE row would navigate away).
      const target = await createConversation(request, headers, agent.id, 'Mgmt target session')
      const keep = await createConversation(request, headers, agent.id, 'Mgmt keep session')

      await page.goto(`/agents/${agent.id}/conversations/${target.id}`)
      await page.waitForLoadState('domcontentloaded')

      const targetRow = page.locator(
        `[data-chat-session-href="/agents/${agent.id}/conversations/${target.id}"]`,
      )
      const keepRow = page.locator(
        `[data-chat-session-href="/agents/${agent.id}/conversations/${keep.id}"]`,
      )
      await expect(targetRow).toBeVisible({ timeout: 15_000 })
      await expect(keepRow).toBeVisible({ timeout: 15_000 })

      const openRowMenu = async (row: typeof targetRow) => {
        await row.hover()
        await row.getByRole('button', { name: '대화 메뉴' }).click()
      }
      const conversationStatus = async (id: string) =>
        (await request.get(`${API_BASE}/api/conversations/${id}`)).status()
      const conversationField = async (id: string, field: 'is_pinned' | 'title') => {
        const response = await request.get(`${API_BASE}/api/conversations/${id}`)
        await expectOk(response, 'conversation get')
        return (await readObject(response, 'conversation'))[field]
      }

      // ── Pin ── exact:true so the '고정' menuitem is not matched by '고정 해제'.
      await openRowMenu(targetRow)
      await page.getByRole('menuitem', { name: '고정', exact: true }).click()
      await expect
        .poll(() => conversationField(target.id, 'is_pinned'), { timeout: 15_000 })
        .toBe(true)
      // The menu now offers Unpin — proves the optimistic + server pin landed.
      await openRowMenu(targetRow)
      await expect(page.getByRole('menuitem', { name: '고정 해제', exact: true })).toBeVisible()
      await page.keyboard.press('Escape')

      // ── Rename ──
      const newTitle = `Renamed mgmt ${Date.now()}`
      await openRowMenu(targetRow)
      await page.getByRole('menuitem', { name: '이름 변경', exact: true }).click()
      const renameDialog = page.getByRole('dialog')
      const renameInput = renameDialog.getByPlaceholder('대화 제목 입력')
      await expect(renameInput).toBeVisible({ timeout: 10_000 })
      await renameInput.fill(newTitle)
      await renameDialog.getByRole('button', { name: '저장', exact: true }).click()
      await expect(page.getByText(newTitle).first()).toBeVisible({ timeout: 15_000 })
      await expect
        .poll(() => conversationField(target.id, 'title'), { timeout: 15_000 })
        .toBe(newTitle)

      // ── Delete (the non-active `keep` row, so the page does not navigate away) ──
      await openRowMenu(keepRow)
      await page.getByRole('menuitem', { name: '삭제', exact: true }).click()
      const confirmDialog = page.getByRole('alertdialog')
      await confirmDialog.getByRole('button', { name: '삭제', exact: true }).click()
      await expect(keepRow).toHaveCount(0, { timeout: 15_000 })
      await expect.poll(() => conversationStatus(keep.id), { timeout: 15_000 }).toBe(404)

      // The pinned + renamed target survived the delete.
      await expect(targetRow).toBeVisible()

      expect(errors.console).toEqual([])
      // The polled 404s above are issued via the APIRequestContext (`request`),
      // not the page, so they never reach page.on('response'); a clean
      // errors.network keeps this test as strong as the sibling above.
      expect(errors.network).toEqual([])
    } finally {
      const cleanupHeaders = await loginApi(request)
      await deleteAgent(request, cleanupHeaders, agent.id)
    }
  })

  test('agent group loads a second keyset page of conversations on demand', async ({
    page,
    request,
    errors,
  }) => {
    // 31 sequential creates + a cold Next dev first-navigation app compile +
    // multi-step paging. The first goto bears the whole-app initial compile.
    test.setTimeout(180_000)
    const headers = await loginApi(request)
    const modelId = await firstModelId(request)
    const agentName = `E2E Navigator Paging ${Date.now()}`
    const agent = await createAgent(request, headers, modelId, agentName)

    try {
      // Backend page size is 30 and the keyset sort is is_pinned DESC, updated_at
      // DESC. Creating 31 conversations sequentially makes the FIRST one (oldest
      // updated_at) sort last, so it lives only on server page 2 — fetched only
      // after the cursor crosses the boundary.
      let oldestId = ''
      for (let index = 0; index < 31; index += 1) {
        const conv = await createConversation(
          request,
          headers,
          agent.id,
          `Paging session ${String(index).padStart(2, '0')}`,
        )
        if (index === 0) oldestId = conv.id
      }

      // Render the chat navigator from a LIGHT route (not the heavy conversation
      // page, whose cold dev compile + chat runtime can stall navigation). The
      // navigator sidebar is global on non-settings routes; on /tools no agent is
      // active, so this agent's group starts collapsed and we expand it by hand.
      await page.goto('/tools', { waitUntil: 'domcontentloaded', timeout: 120_000 })

      const agentRows = page.locator(
        `[data-chat-session-href^="/agents/${agent.id}/conversations/"]`,
      )
      const oldestRow = page.locator(
        `[data-chat-session-href="/agents/${agent.id}/conversations/${oldestId}"]`,
      )
      const loadMore = page.getByRole('button', { name: '더 보기' })

      // Expand this agent's group (scoped by its unique name) — collapsed groups do
      // not fetch conversations, so this triggers the first keyset page.
      await page
        .locator('div', { has: page.getByRole('link', { name: agentName, exact: true }) })
        .getByRole('button', { name: '에이전트 펼치기' })
        .first()
        .click({ timeout: 20_000 })

      // Collapsed view caps at DEFAULT_SESSION_CAP (5); the page-2 conv is absent.
      await expect(agentRows).toHaveCount(5, { timeout: 20_000 })
      await expect(oldestRow).toHaveCount(0)

      // 1st "더 보기" reveals the rest of the already-loaded first page (5 → 30,
      // client-side only) — the page-2 conv is still not fetched.
      await loadMore.click({ timeout: 15_000 })
      await expect(agentRows).toHaveCount(30, { timeout: 15_000 })
      await expect(oldestRow).toHaveCount(0)

      // 2nd "더 보기" fetches the next keyset page (30 → 31). The oldest conv, which
      // lives only on page 2, now renders — proving the cursor crossed the boundary.
      await loadMore.click({ timeout: 15_000 })
      await expect(agentRows).toHaveCount(31, { timeout: 15_000 })
      await expect(oldestRow).toBeVisible()

      // With no further pages the load-more control flips to Collapse. exact:true so
      // it is not matched by the agent group's "에이전트 접기" chevron.
      await expect(page.getByRole('button', { name: '접기', exact: true })).toBeVisible()

      expect(errors.console).toEqual([])
      expect(errors.network).toEqual([])
    } finally {
      const cleanupHeaders = await loginApi(request)
      await deleteAgent(request, cleanupHeaders, agent.id)
    }
  })

  test('row menu shares then revokes a conversation through the dialog', async ({
    page,
    request,
    errors,
  }) => {
    // Light-route navigation + cold first-nav compile; see the paging test. The
    // ShareDialog's `share` namespace is now root-scoped, so it renders translated
    // here too (not only on the heavy chat route).
    test.setTimeout(180_000)
    const headers = await loginApi(request)
    const modelId = await firstModelId(request)
    const agentName = `E2E Navigator Share ${Date.now()}`
    const agent = await createAgent(request, headers, modelId, agentName)

    try {
      const conv = await createConversation(request, headers, agent.id, 'Share target session')

      await page.goto('/tools', { waitUntil: 'domcontentloaded', timeout: 120_000 })

      // Expand this agent's group (scoped by its unique name) to reveal its rows.
      await page
        .locator('div', { has: page.getByRole('link', { name: agentName, exact: true }) })
        .getByRole('button', { name: '에이전트 펼치기' })
        .first()
        .click({ timeout: 20_000 })

      const row = page.locator(
        `[data-chat-session-href="/agents/${agent.id}/conversations/${conv.id}"]`,
      )
      await expect(row).toBeVisible({ timeout: 20_000 })

      // Open the row menu → 공유 → ShareDialog.
      await row.hover()
      await row.getByRole('button', { name: '대화 메뉴' }).click()
      await page.getByRole('menuitem', { name: '공유', exact: true }).click()

      const dialog = page.getByRole('dialog')
      const createButton = dialog.getByRole('button', { name: '공유 링크 만들기', exact: true })
      const revokeButton = dialog.getByRole('button', { name: '공유 해제', exact: true })

      // A private conversation opens on the create action.
      await expect(createButton).toBeVisible({ timeout: 15_000 })

      // Create the public link → the dialog flips to the shared state (revoke action).
      await createButton.click()
      await expect(revokeButton).toBeVisible({ timeout: 15_000 })

      // The dialog surfaces the real public link; capture its token.
      const shareUrl = await dialog.getByRole('textbox', { name: '공유 링크' }).inputValue()
      const token = shareUrl.split('/shared/')[1] ?? ''
      expect(token, 'share token in dialog link').toBeTruthy()

      // Revoke → the dialog flips back to private and the public link 404s.
      await revokeButton.click()
      await expect(createButton).toBeVisible({ timeout: 15_000 })
      await expect
        .poll(async () => (await request.get(`${API_BASE}/api/shares/${token}`)).status(), {
          timeout: 15_000,
        })
        .toBe(404)

      expect(errors.console).toEqual([])
      expect(errors.network).toEqual([])
    } finally {
      const cleanupHeaders = await loginApi(request)
      await deleteAgent(request, cleanupHeaders, agent.id)
    }
  })
})
