import { test, expect } from './fixtures'
import type { APIRequestContext, Locator, Page, Request } from '@playwright/test'
import { z } from 'zod'
import { readAgUiAcceptance, readAgUiRunInput } from './helpers/ag-ui-run-receipt'
import { observeApprovalCard } from './helpers/approval-card-observations'

// HITL tool approval — reject path. execute_in_skill carries a default
// interrupt policy (tool risk metadata), so a scripted document run pauses on
// an approval card before the tool executes. The approve path is covered by
// document-artifact-viewers; this asserts the reject path: rejecting skips the
// tool, so no artifact is produced. Setup mirrors the document spec (install a
// system-seed skill, attach it, drive the keyless scripted model via E2E_DOCX).
const API =
  process.env.E2E_API_BASE_URL ?? `http://localhost:${process.env.E2E_BACKEND_PORT ?? '8001'}`
const EMAIL = process.env.E2E_USER_EMAIL ?? process.env.E2E_EMAIL ?? 'playwright-e2e@moldy.dev'
const PASSWORD =
  process.env.E2E_USER_PASSWORD ?? process.env.E2E_PASSWORD ?? 'correct horse battery staple 42'

async function login(request: APIRequestContext): Promise<Record<string, string>> {
  const res = await request.post(`${API}/api/auth/login`, {
    data: { email: EMAIL, password: PASSWORD },
  })
  expect(res.ok()).toBeTruthy()
  return { 'X-CSRF-Token': (await res.json()).csrf_token as string }
}

async function getJson<T>(request: APIRequestContext, url: string): Promise<T> {
  const res = await request.get(url)
  expect(res.ok(), `GET ${url} → ${res.status()}`).toBeTruthy()
  return (await res.json()) as T
}

const pendingTool = z.object({
  threadId: z.string(),
  runStatus: z.literal('interrupted'),
  activeRunId: z.null(),
  checkpointId: z.string().min(1),
  interruptRunId: z.string().min(1),
  physicalInterruptRunId: z.string().uuid(),
  interrupts: z.tuple([
    z.object({
      id: z.string().min(1),
      reason: z.literal('moldy.tool_approval'),
      subagentRunId: z.null().optional(),
    }),
  ]),
  messages: z.array(
    z.object({
      id: z.string(),
      role: z.string(),
      subagentRunId: z.string().nullish(),
      toolCalls: z
        .array(z.object({ id: z.string(), function: z.object({ name: z.string() }) }))
        .nullish(),
    }),
  ),
  messageLinks: z.array(z.object({ messageId: z.string(), physicalRunId: z.string().nullish() })),
})

async function confirmNativeRejection(
  page: Page,
  request: APIRequestContext,
  conversationId: string,
  firstCommand: Request,
  confirm: Locator,
) {
  const api = `${API}/api/conversations/${conversationId}`
  const initial = readAgUiRunInput(firstCommand, conversationId)
  expect(initial?.resume ?? []).toEqual([])
  const pending = pendingTool.parse(await getJson(request, api + '/ag-ui/snapshot'))
  expect(pending.threadId).toBe(conversationId)
  expect(pending.interruptRunId).toBe(initial?.runId)
  const parentId = pending.physicalInterruptRunId
  const linked = new Set(
    pending.messageLinks
      .filter((link) => link.physicalRunId === parentId)
      .map((link) => link.messageId),
  )
  const issuers = pending.messages
    .filter(
      (message) => message.role === 'assistant' && !message.subagentRunId && linked.has(message.id),
    )
    .flatMap((message) =>
      (message.toolCalls ?? [])
        .filter((call) => call.function.name === 'execute_in_skill')
        .map((call) => ({ messageId: message.id, toolCallId: call.id })),
    )
  expect(issuers).toHaveLength(1)
  const interruptId = pending.interrupts[0].id
  const [response] = await Promise.all([
    page.waitForResponse((r) => r.request().method() === 'POST' && r.url() === api + '/ag-ui/runs'),
    confirm.click(),
  ])
  const submitted = readAgUiRunInput(response.request(), conversationId)
  expect(submitted?.messages).toEqual([])
  expect(submitted?.resume).toMatchObject([
    {
      interruptId,
      status: 'resolved',
      payload: { decisions: [{ type: 'reject' }] },
    },
  ])
  const accepted = await readAgUiAcceptance(response)
  expect(accepted.publicRunId).not.toBe(initial?.runId)
  expect(accepted.physicalRunId).toBeTruthy()
  expect(accepted.physicalRunId).not.toBe(parentId)
  await expect
    .poll(
      async () => {
        const { runs } = await getJson<{
          runs: { id: string; parent_run_id: string | null; status: string; source: string }[]
        }>(request, api + '/history')
        return {
          total: runs.length,
          children: runs
            .filter((run) => run.parent_run_id === parentId)
            .map((run) => [run.id, run.source, run.status]),
        }
      },
      { timeout: 30_000 },
    )
    .toEqual({ total: 2, children: [[accepted.physicalRunId, 'resume', 'completed']] })
  return { ...issuers[0], physicalRunId: parentId, interruptId, actionIndex: 0, decision: 'reject' }
}

test.describe('HITL tool approval — reject', () => {
  test.skip(process.env.PW_SKIP_BACKEND === '1', 'Requires the FastAPI backend')

  let csrf: Record<string, string>
  let agentId: string
  let conversationId: string

  test.beforeAll(async ({ request }) => {
    csrf = await login(request)

    // Install the seeded docx skill so execute_in_skill is available.
    const items = await getJson<
      { id: string; slug: string; installation: { installed_resource_id?: string | null } }[]
    >(request, `${API}/api/marketplace/items?resource_type=skill&source_kind=system_seed&limit=200`)
    const docx = items.find((i) => i.slug === 'docx-document')
    if (!docx) throw new Error('docx-document skill should be seeded')
    const install = (await (
      await request.post(`${API}/api/marketplace/items/${docx.id}/install`, {
        headers: csrf,
        data: { install_mode: 'overwrite_existing' },
      })
    ).json()) as { installed_skill_id?: string | null }
    const skillId = install.installed_skill_id ?? docx.installation.installed_resource_id
    expect(skillId).toBeTruthy()

    const models = await getJson<{ id: string; provider: string; model_name: string }[]>(
      request,
      `${API}/api/models`,
    )
    const scripted = models.find(
      (m) => m.provider === 'e2e_scripted' && m.model_name === 'document-artifact-scripted',
    )
    if (!scripted) throw new Error('document-artifact-scripted model should be seeded')

    const agent = (await (
      await request.post(`${API}/api/agents`, {
        headers: csrf,
        data: {
          name: `E2E HITL Reject Agent ${Date.now()}`,
          system_prompt: 'Use execute_in_skill exactly when the scripted model requests it.',
          model_id: scripted.id,
          skill_ids: [skillId],
        },
      })
    ).json()) as { id: string }
    agentId = agent.id
    const conv = (await (
      await request.post(`${API}/api/agents/${agentId}/conversations`, {
        headers: csrf,
        data: { title: 'HITL Reject E2E' },
      })
    ).json()) as { id: string }
    conversationId = conv.id
  })

  test.afterAll(async ({ request }) => {
    if (agentId) await request.delete(`${API}/api/agents/${agentId}`, { headers: csrf })
  })

  test('rejecting an execute_in_skill approval skips the tool', async ({ page, request }) => {
    test.setTimeout(120_000)
    const observer = observeApprovalCard(page, { apiBase: API, conversationId, agentId })
    const native = process.env.NEXT_PUBLIC_CHAT_RUNTIME === 'ag_ui'
    const commands: Request[] = []
    let legacyWrites = 0
    const api = `${API}/api/conversations/${conversationId}`
    if (native)
      page.on('request', (req) => {
        if (req.method() !== 'POST') return
        if (req.url() === api + '/ag-ui/runs') commands.push(req)
        if (
          req.url() === api + '/messages' ||
          req.url().startsWith(api + '/messages/') ||
          req.url() === api + '/langgraph/threads/' + conversationId + '/commands'
        )
          legacyWrites++
      })
    try {
      await page.goto(`/agents/${agentId}/conversations/${conversationId}`)
      const form = page.locator('form[data-moldy-thread-composer]').last()
      const composer = form.locator('textarea[data-moldy-composer-input="true"]')
      await expect(composer).toBeVisible()
      await composer.fill('mcp 도구 사용 승인 HITL')
      await expect(form.locator('button[type="submit"]')).toBeEnabled({ timeout: 30_000 })
      await composer.press('Enter')

      await expect(page.getByText('승인이 필요합니다').last()).toBeVisible({ timeout: 30_000 })
      await page.getByRole('button', { name: '거부', exact: true }).last().click()
      const confirm = page.getByRole('button', { name: '거부 확인' }).last()
      await expect(confirm).toBeEnabled()
      let expectedDecision
      if (native) {
        expect(commands.length).toBe(1)
        expectedDecision = await confirmNativeRejection(
          page,
          request,
          conversationId,
          commands[0],
          confirm,
        )
      } else {
        await confirm.click()
      }
      const rejectedBadge = page.getByText('거부됨').last()
      await expect(rejectedBadge).toBeVisible({ timeout: 30_000 })
      await expect(page.getByText('승인 응답을 전송하지 못했습니다. 다시 시도하세요.')).toHaveCount(
        0,
      )
      await expect
        .poll(
          async () =>
            (await getJson<{ status: string } | null>(request, api + '/runs/active'))?.status ??
            'idle',
          { timeout: 30_000 },
        )
        .toBe('idle')
      const artifacts = await getJson<{ display_name: string }[]>(request, api + '/artifacts')
      expect(artifacts.map((a) => a.display_name)).not.toContain('moldy-docx-demo.docx')

      if (expectedDecision) {
        expect(commands.length).toBe(2)
        const [snapshot] = await Promise.all([
          page.waitForResponse(
            (r) => r.request().method() === 'GET' && r.url() === api + '/ag-ui/snapshot',
          ),
          page.reload(),
        ])
        expect(snapshot.ok()).toBe(true)
        const data = z
          .object({
            threadId: z.string(),
            interrupts: z.array(z.unknown()),
            approvalDecisions: z.tuple([
              z.object({
                messageId: z.string(),
                toolCallId: z.string(),
                physicalRunId: z.string(),
                interruptId: z.string(),
                actionIndex: z.number().int(),
                decision: z.literal('reject'),
                subagentRunId: z.null().optional(),
              }),
            ]),
          })
          .parse(await snapshot.json())
        expect(data.threadId).toBe(conversationId)
        expect(data.interrupts).toEqual([])
        expect(data.approvalDecisions[0]).toMatchObject(expectedDecision)
        await expect(rejectedBadge).toBeVisible({ timeout: 30_000 })
        await expect(page.getByRole('button', { name: '거부 확인' })).toHaveCount(0)
        expect(commands.length).toBe(2)
        expect(legacyWrites).toBe(0)
      }
    } catch (error) {
      await observer.attachOnFailure(test.info()).catch(() => undefined)
      throw error
    } finally {
      observer.dispose()
    }
  })
})
