import { createHash, randomUUID } from 'node:crypto'
import { readFile } from 'node:fs/promises'
import type { APIRequestContext, BrowserContext, Page, TestInfo } from '@playwright/test'
import {
  API_BASE,
  E2E_EMAIL,
  E2E_PASSWORD,
  apiDeleteOk,
  apiGetJson,
  apiJson,
  apiPostJson,
  expect,
  isRecord,
  loginApi,
  test,
  type CsrfHeaders,
} from './fixtures'
import { isSeededScriptedModel } from './helpers/scripted-model'
import { records, sendMessageForRun, stringField, waitForRunStatus } from './langgraph-v3-helpers'

const delegatedBrief = 'E2E_SUBAGENT_SLOW summarize scoped LangGraph v3 work.'
const resultFile = 'e2e-runtime-policy-artifact.md'
const resultBytes = 'E2E runtime artifact final content.\n'

function object(value: unknown): Record<string, unknown> {
  if (!isRecord(value)) throw new Error('Expected a JSON object')
  return value
}

async function collectChildProof(
  chat: Page,
  request: APIRequestContext,
  testInfo: TestInfo,
  ids: { conversationId: string; homeOrgId: string; goalId: string; originalRun: string },
) {
  const base = `${API_BASE}/api/conversations/${ids.conversationId}`
  const rows = (values: unknown) => (Array.isArray(values) ? values.filter(isRecord) : [])
  async function scopedRead(url: string) {
    const response = await request.get(url, { timeout: 5000 }).catch(() => null)
    const data: unknown = response?.ok() ? await response.json().catch(() => null) : null
    return { status: response?.status() ?? 0, body: isRecord(data) ? data : {} }
  }
  const pills = chat.locator('.moldy-tool-pill')
  const [goal, run, snapshot, pillCount, expectedBriefVisible, steerButtonVisible] =
    await Promise.all([
      scopedRead(`${base}/goal`),
      scopedRead(`${base}/runs/${ids.originalRun}`),
      scopedRead(`${base}/ag-ui/snapshot`),
      pills.count(),
      pills.getByText(delegatedBrief, { exact: true }).first().isVisible(),
      chat.locator('[data-moldy-run-steer-send]').isVisible(),
    ])
  const children = rows(snapshot.body.subagents).filter(
    (child) => child.physicalRunId === ids.originalRun && !child.parentSubagentRunId,
  )
  const bound = rows(snapshot.body.messages)
    .filter((message) => message.role === 'assistant' && !message.subagentRunId)
    .flatMap((message) =>
      rows(message.toolCalls).filter(
        (call) =>
          children.some(
            (child) => child.parentMessageId === message.id && child.parentToolCallId === call.id,
          ) &&
          isRecord(call.function) &&
          call.function.name === 'task',
      ),
    )
  const eligibleBriefs = bound.filter((call) => {
    if (!isRecord(call.function) || typeof call.function.arguments !== 'string') return false
    try {
      const args: unknown = JSON.parse(call.function.arguments)
      return isRecord(args) && args.description === delegatedBrief
    } catch {
      return false
    }
  }).length
  await testInfo.attach('goal-live-child-proof', {
    contentType: 'application/json',
    body: JSON.stringify({
      ...ids,
      scopedGoalStatus: goal.status,
      scopedRunStatus: run.status,
      scopedSnapshotStatus: snapshot.status,
      runStatus: run.body.id === ids.originalRun ? run.body.status : null,
      childCount: children.length,
      boundTaskCalls: bound.length,
      eligibleBriefs,
      expectedBriefOnParent: eligibleBriefs > 0,
      pillCount,
      expectedBriefVisible,
      steerButtonVisible,
    }),
  })
}

async function openTab(page: Page, path: string) {
  const popup = page.waitForEvent('popup', { timeout: 12_000 })
  await page.evaluate((url) => {
    window.open(url, '_blank')
  }, path)
  const tab = await popup
  tab.setDefaultTimeout(12_000)
  return tab
}

async function saveGoal(page: Page) {
  const saved = page.waitForResponse(
    (res) => res.request().method() === 'PUT' && new URL(res.url()).pathname.endsWith('/goal'),
    { timeout: 12_000 },
  )
  await page
    .getByRole('dialog')
    .getByRole('button', { name: /^저장$|^Save$/ })
    .click()
  const response = await saved
  const view = object(await apiJson(response, 'save conversation goal'))
  return { url: response.url(), goal: object(view.goal) }
}

async function otherOrganization(request: APIRequestContext, headers: CsrfHeaders) {
  const organizations = records(
    await apiGetJson(request, `${API_BASE}/api/me/organizations`),
    'organizations',
  )
  const home = organizations.find((org) => org.is_active)
  if (!home) throw new Error('Missing active organization')
  const me = object(await apiGetJson(request, `${API_BASE}/api/auth/me`))
  const other = object(
    await apiPostJson(request, `${API_BASE}/api/tenants/${home.tenant_id}/organizations`, headers, {
      name: `Goal result isolation ${randomUUID()}`,
      administrator: { user_id: stringField(me, 'id', 'owner') },
    }),
  )
  return {
    ownerId: stringField(me, 'id', 'owner'),
    homeId: stringField(home, 'id', 'home organization'),
    otherId: stringField(object(other.organization), 'id', 'other organization'),
  }
}

async function matchingGoalNotices(
  request: APIRequestContext,
  goalId: string,
  runId: string,
  versionId: string,
): Promise<string[]> {
  const feed = object(await apiGetJson(request, `${API_BASE}/api/notifications`))
  const found: string[] = []
  for (const item of records(feed.items, 'notifications')) {
    if (item.kind !== 'chat_goal.completed' || !isRecord(item.target) || !item.target.accessible) {
      continue
    }
    const id = stringField(item, 'id', 'notification')
    const target = object(
      await apiGetJson(request, `${API_BASE}/api/notifications/${id}/destination`),
    )
    if (target.goal_id === goalId) {
      expect(target).toMatchObject({ run_id: runId, version_id: versionId })
      found.push(id)
    }
  }
  return found
}

async function browserCsrf(context: BrowserContext) {
  const cookie = (await context.cookies(API_BASE)).find((item) => item.name === 'moldy_csrf')
  if (!cookie?.value) throw new Error('Browser session has no CSRF cookie')
  return cookie.value
}

async function signInToEmptyContext(
  context: BrowserContext,
  previousCsrf: string,
  ownerId: string,
) {
  expect((await context.cookies()).length).toBe(0)
  expect((await context.request.get(`${API_BASE}/api/auth/me`)).status()).toBe(401)
  const inbox = await context.newPage()
  inbox.setDefaultTimeout(12_000)
  await inbox.goto('/login')
  await inbox.locator('#login-email').fill(E2E_EMAIL)
  await inbox.locator('#login-password').fill(E2E_PASSWORD)
  const response = inbox.waitForResponse(
    (res) => res.request().method() === 'POST' && new URL(res.url()).pathname === '/api/auth/login',
  )
  await inbox.getByRole('button', { name: /^(로그인|Log in|Sign in)$/i }).click()
  const auth = object(await apiJson(await response, 'fresh browser login'))
  expect(stringField(object(auth.user), 'id', 'signed-in user')).toBe(ownerId)
  const csrf = stringField(auth, 'csrf_token', 'login session')
  expect(csrf === (await browserCsrf(context)) && csrf !== previousCsrf).toBe(true)
  await inbox.waitForURL((url) => url.pathname === '/')
  await expect(
    inbox.getByRole('button', { name: process.env.E2E_USER_NAME ?? 'E2E User' }).first(),
  ).toBeVisible()
  const onboarding = inbox.getByRole('dialog', {
    name: /Moldy에 오신 것을 환영합니다|Welcome to Moldy/i,
  })
  await inbox.addLocatorHandler(onboarding, async () => {
    await onboarding.getByRole('button', { name: /^(나중에|Later)$/i }).click()
  })
  return { inbox, headers: { 'X-CSRF-Token': csrf } }
}

test('a goal edited during a tool call delivers one verified result after a fresh sign-in', async ({
  page,
  request,
  browser,
}, testInfo) => {
  test.skip(
    process.env.E2E_CHAT_GOAL_NOTIFICATIONS !== '1',
    'Requires the goal and notification enabled scripted lane',
  )
  test.setTimeout(180_000)
  page.setDefaultTimeout(12_000)
  const headers = await loginApi(request)
  const { homeId, otherId, ownerId } = await otherOrganization(request, headers)
  const models = records(await apiGetJson(request, `${API_BASE}/api/models`), 'models')
  const model = models.find(isSeededScriptedModel)
  if (!model) throw new Error('Missing seeded scripted model')
  const createAgent = async (name: string, extra: Record<string, unknown>) =>
    object(
      await apiPostJson(request, `${API_BASE}/api/agents`, headers, {
        name: `${name} ${randomUUID()}`,
        model_id: model.id,
        ...extra,
      }),
    )
  const child = await createAgent('Research child', {
    system_prompt: 'Return the requested scoped answer.',
  })
  const childId = stringField(child, 'id', 'child')
  let parentId: string | undefined
  let fresh: BrowserContext | undefined
  try {
    const agent = await createAgent('Goal results', {
      sub_agent_ids: [childId],
      system_prompt:
        'Use the child for research and the conversation workspace for the final file.',
      runtime_policy: {
        version: 1,
        filesystem: { mode: 'artifact_write' },
        todo: { enabled: true },
        summarization: { mode: 'auto' },
      },
    })
    parentId = stringField(agent, 'id', 'parent')
    await page.goto('/agents')
    const chat = await openTab(page, `/agents/${parentId}/conversations/new`)
    const card = chat.locator('#conversation-goal')
    await card.getByRole('button', { name: /목표 만들기|Create goal/i }).click()
    await chat
      .getByRole('dialog')
      .getByRole('textbox', { name: /목표 이름|Goal name/i })
      .fill('Collect a first draft')
    await chat
      .getByRole('dialog')
      .getByRole('textbox', { name: /결과 파일 경로|file path/i })
      .fill('original.md')
    const created = await saveGoal(chat)
    expect(created.goal).toMatchObject({ version: 1, status: 'active', last_evidence: null })
    const base = created.url.replace(/\/goal$/, '')
    const cid = new URL(base).pathname.split('/').at(-1)
    if (!cid) throw new Error('Draft goal save did not return its conversation ID')
    const chatPath = `/agents/${parentId}/conversations/${cid}`
    await expect(chat).toHaveURL(new RegExp(`${chatPath}$`))
    const history = object(await apiGetJson(request, `${base}/messages`))
    expect(history.messages).toEqual([])

    // Prepare the revised criteria in a second tab. Save only once the first run has started.
    const editor = await openTab(chat, `${chatPath}#conversation-goal`)
    const editingCard = editor.locator('#conversation-goal')
    await editingCard.getByRole('button', { name: /목표 수정|Edit goal/i }).click()
    await editor
      .getByRole('dialog')
      .getByRole('textbox', { name: /목표 이름|Goal name/i })
      .fill(`E2E_RUNTIME_FILESYSTEM_ARTIFACT_WRITE in /conversations/${cid}/`)
    await editor
      .getByRole('dialog')
      .getByRole('textbox', { name: /결과 파일 경로|file path/i })
      .fill(resultFile)

    const runId = await sendMessageForRun(
      chat,
      cid,
      `E2E_LANGGRAPH_V3 slow_subagent=true subagent=${stringField(child, 'runtime_name', 'child')}`,
    )
    const revised = await saveGoal(editor)
    expect(revised.goal).toMatchObject({
      id: created.goal.id,
      version: 2,
      status: 'active',
      last_evidence: null,
    })
    await editor.close()
    const proofIds = {
      conversationId: cid,
      homeOrgId: homeId,
      goalId: stringField(created.goal, 'id', 'goal'),
      originalRun: runId,
    }
    const pill = chat.locator('.moldy-tool-pill').getByText(delegatedBrief, { exact: true }).first()
    try {
      await expect(pill).toBeVisible()
    } catch (error) {
      await collectChildProof(chat, request, testInfo, proofIds)
      throw error
    }
    const steerUrl = `${base}/runs/${runId}/steers`
    const [submitted] = await Promise.all([
      (async () => {
        await chat
          .locator('textarea[data-moldy-composer-input="true"]')
          .last()
          .fill('E2E_DAILY_GREETING use the latest brief.')
        const receipt = chat.waitForResponse(
          (res) => res.url() === steerUrl && res.request().method() === 'POST',
        )
        await chat.locator('[data-moldy-run-steer-send]').click()
        return object(await apiJson(await receipt, 'submit active instruction'))
      })(),
      collectChildProof(chat, request, testInfo, proofIds),
    ])
    expect(submitted.status).toBe('pending')
    await chat.close()
    await waitForRunStatus(request, cid, runId, 'completed')
    await expect
      .poll(async () =>
        records(await apiGetJson(request, steerUrl), 'steers').map((r) => [r.id, r.status]),
      )
      .toEqual([[submitted.id, 'applied']])

    let evidence: Record<string, unknown> | undefined
    await expect
      .poll(
        async () => {
          const view = object(await apiGetJson(request, created.url))
          const goal = object(view.goal)
          if (goal.status !== 'completed') return false
          expect(goal).toMatchObject({ id: created.goal.id, version: 2, auto_followups: 1 })
          const checks = records(object(goal.last_evidence).checks, 'goal evidence')
          expect(checks).toHaveLength(1)
          expect(checks[0]).toMatchObject({
            passed: true,
            logical_path: resultFile,
            sha256: createHash('sha256').update(resultBytes).digest('hex'),
          })
          evidence = checks[0]
          return true
        },
        { timeout: 75_000, intervals: [500, 1000] },
      )
      .toBe(true)
    if (!evidence) throw new Error('Goal is missing verified artifact evidence')
    const verifiedRun = stringField(evidence, 'source_run_id', 'evidence')
    const versionId = stringField(evidence, 'version_id', 'evidence')
    expect(verifiedRun).not.toBe(runId)
    const versionUrl = `${base}/goal/artifacts/${stringField(evidence, 'artifact_id', 'evidence')}/versions/${versionId}/download`
    let noticeId = ''
    await expect
      .poll(
        async () => {
          const found = await matchingGoalNotices(
            request,
            stringField(created.goal, 'id', 'goal'),
            verifiedRun,
            versionId,
          )
          noticeId = found[0] ?? ''
          return found.length
        },
        { timeout: 65_000 },
      )
      .toBe(1)

    const frontendOrigin = new URL(page.url()).origin
    const previousCsrf = await browserCsrf(page.context())
    await page.close()
    fresh = await browser.newContext({
      baseURL: frontendOrigin,
      storageState: { cookies: [], origins: [] },
    })
    const signedIn = await signInToEmptyContext(fresh, previousCsrf, ownerId)
    const { inbox } = signedIn
    expect(
      await matchingGoalNotices(
        fresh.request,
        stringField(created.goal, 'id', 'goal'),
        verifiedRun,
        versionId,
      ),
    ).toEqual([noticeId])
    await inbox.goto('/agents')
    await inbox.getByTestId('notifications-bell').click()
    const notice = inbox.locator(`[data-notification-id="${noticeId}"]`)
    const downloadEvent = inbox.waitForEvent('download')
    await notice.click()
    const download = await downloadEvent
    expect(download.suggestedFilename()).toBe(resultFile)
    const local = await download.path()
    if (!local) throw new Error('Missing downloaded file')
    expect(await readFile(local, 'utf8')).toBe(resultBytes)
    await expect(inbox).toHaveURL(new RegExp(`${chatPath}#conversation-goal$`))
    await inbox.reload()
    await inbox.getByTestId('notifications-bell').click()
    await expect(notice).toBeVisible()
    await expect(notice).not.toHaveAttribute('data-unread', 'true')

    await apiPostJson(fresh.request, `${API_BASE}/api/me/active-org`, signedIn.headers, {
      org_id: otherId,
    })
    const foreign = object(await apiGetJson(fresh.request, `${API_BASE}/api/notifications`))
    const foreignGoalNotices = records(foreign.items, 'other inbox').filter(
      (item) => item.id === noticeId,
    )
    expect(foreignGoalNotices).toHaveLength(0)
    expect((await fresh.request.get(versionUrl)).status()).toBe(404)
    expect(
      (await fresh.request.get(`${API_BASE}/api/notifications/${noticeId}/destination`)).status(),
    ).toBe(404)
    await testInfo.attach('goal-journey-proof', {
      contentType: 'application/json',
      body: JSON.stringify({
        conversationId: cid,
        orgId: homeId,
        artifactId: stringField(evidence, 'artifact_id', 'evidence'),
        originalRun: runId,
        steerId: submitted.id,
        steerStatus: 'applied',
        goalId: created.goal.id,
        goalVersion: 2,
        automaticFollowups: 1,
        freshLogin: true,
        anonymousStatusBeforeLogin: 401,
        sameSingleNoticeAfterLogin: true,
        verifiedRun,
        versionId,
        sha256: evidence.sha256,
        noticeId,
        completionNotices: 1,
        foreignGoalNotices: foreignGoalNotices.length,
        foreignVersionStatus: 404,
        foreignDestinationStatus: 404,
      }),
    })
  } finally {
    await fresh?.close()
    await apiPostJson(request, `${API_BASE}/api/me/active-org`, headers, { org_id: homeId })
    if (parentId) await apiDeleteOk(request, `${API_BASE}/api/agents/${parentId}`, headers)
    await apiDeleteOk(request, `${API_BASE}/api/agents/${childId}`, headers)
  }
})
