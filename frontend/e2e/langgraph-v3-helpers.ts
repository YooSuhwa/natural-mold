import type { APIRequestContext, Page } from '@playwright/test'
import {
  API_BASE,
  apiGetJson,
  apiPostJson,
  expect,
  isRecord,
  loginApi,
  type CsrfHeaders,
} from './fixtures'
import { commandMethod, waitForAcceptedRunStart } from './helpers/run-start'

export { commandMethod, waitForAcceptedRunStart }

export const DOCX_SKILL_SLUG = 'docx-document'
export const SCRIPTED_PROVIDER = 'e2e_scripted'
export const SCRIPTED_MODEL = 'document-artifact-scripted'
export const FINAL_TEXT = 'E2E LangGraph v3 validation complete'
export const REPORT_FILE = 'moldy-langgraph-v3-report.md'
export const NOTES_FILE = 'moldy-langgraph-v3-notes.txt'
const CHAT_COMPOSER_TIMEOUT_MS = 45_000
const ARTIFACT_INDEX_TIMEOUT_MS = 75_000

export interface LangGraphV3Setup {
  readonly parentAgentId: string
  readonly childAgentId: string
  readonly childRuntimeName: string
  readonly childName: string
  readonly conversationId: string
  readonly csrfHeaders: CsrfHeaders
}

export function records(value: unknown, label: string): Record<string, unknown>[] {
  if (Array.isArray(value) && value.every(isRecord)) return value
  throw new Error(`${label} did not return a record array`)
}

export function stringField(record: Record<string, unknown>, key: string, label: string): string {
  const value = record[key]
  if (typeof value === 'string' && value) return value
  throw new Error(`${label} did not include ${key}`)
}

async function scriptedModelId(request: APIRequestContext): Promise<string> {
  const models = records(await apiGetJson(request, `${API_BASE}/api/models`), 'models')
  const model = models.find(
    (row) => row.provider === SCRIPTED_PROVIDER && row.model_name === SCRIPTED_MODEL,
  )
  if (!model) throw new Error('E2E scripted model is not seeded')
  return stringField(model, 'id', 'scripted model')
}

async function docxMarketplaceItem(request: APIRequestContext): Promise<Record<string, unknown>> {
  const items = records(
    await apiGetJson(
      request,
      `${API_BASE}/api/marketplace/items?resource_type=skill&source_kind=system_seed&limit=200`,
    ),
    'marketplace items',
  )
  const item = items.find((row) => row.slug === DOCX_SKILL_SLUG)
  if (!item) throw new Error(`Missing marketplace item: ${DOCX_SKILL_SLUG}`)
  return item
}

async function installDocxSkill(
  request: APIRequestContext,
  csrfHeaders: CsrfHeaders,
): Promise<string> {
  const item = await docxMarketplaceItem(request)
  const installed = await apiPostJson(
    request,
    `${API_BASE}/api/marketplace/items/${stringField(item, 'id', 'docx skill')}/install`,
    csrfHeaders,
    { install_mode: 'overwrite_existing' },
  )

  if (!isRecord(installed)) throw new Error('skill install did not return an object')
  const id = typeof installed.installed_skill_id === 'string' ? installed.installed_skill_id : null
  if (!id) throw new Error('skill install did not return a skill id')
  return id
}

async function createAgent(
  request: APIRequestContext,
  csrfHeaders: CsrfHeaders,
  data: Record<string, unknown>,
): Promise<Record<string, unknown>> {
  const agent = await apiPostJson(request, `${API_BASE}/api/agents`, csrfHeaders, data)
  if (!isRecord(agent)) throw new Error('agent create did not return an object')
  return agent
}

export async function setupLangGraphV3Agent(request: APIRequestContext): Promise<LangGraphV3Setup> {
  const csrfHeaders = await loginApi(request)
  const modelId = await scriptedModelId(request)
  const skillId = await installDocxSkill(request, csrfHeaders)
  const unique = Date.now()
  const child = await createAgent(request, csrfHeaders, {
    name: `E2E LangGraph v3 Child ${unique}`,
    description: 'Deterministic delegated subagent for LangGraph v3 E2E.',
    system_prompt: 'Return the deterministic E2E_SUBAGENT response.',
    model_id: modelId,
    tool_ids: [],
    mcp_tool_ids: [],
    skill_ids: [],
    sub_agent_ids: [],
    middleware_configs: [],
  })
  const parent = await createAgent(request, csrfHeaders, {
    name: `E2E LangGraph v3 Agent ${unique}`,
    description: 'Deterministic LangGraph v3 runtime E2E fixture.',
    system_prompt: 'Use write_todos, the delegated child subagent, and execute_in_skill.',
    model_id: modelId,
    tool_ids: [],
    mcp_tool_ids: [],
    skill_ids: [skillId],
    sub_agent_ids: [stringField(child, 'id', 'child agent')],
    middleware_configs: [],
  })
  const conversation = await apiPostJson(
    request,
    `${API_BASE}/api/agents/${stringField(parent, 'id', 'parent agent')}/conversations`,
    csrfHeaders,
    { title: 'LangGraph v3 E2E conversation' },
  )
  if (!isRecord(conversation)) throw new Error('conversation create did not return an object')
  return {
    parentAgentId: stringField(parent, 'id', 'parent agent'),
    childAgentId: stringField(child, 'id', 'child agent'),
    childRuntimeName: stringField(child, 'runtime_name', 'child agent'),
    childName: stringField(child, 'name', 'child agent'),
    conversationId: stringField(conversation, 'id', 'conversation'),
    csrfHeaders,
  }
}

export async function sendMessage(page: Page, text: string): Promise<void> {
  const composer = page.locator('textarea[data-moldy-composer-input="true"]').last()
  await expect(composer).toBeVisible({ timeout: CHAT_COMPOSER_TIMEOUT_MS })
  await expect(composer).toBeEnabled({ timeout: CHAT_COMPOSER_TIMEOUT_MS })
  await composer.fill(text)
  await composer.press('Enter')
  const remainingText = await composer.inputValue().catch(() => '')
  if (remainingText.trim().length === 0) return

  const sendButton = page.getByRole('button', { name: /전송|Send Button|Send/ }).last()
  await expect(sendButton).toBeEnabled({ timeout: CHAT_COMPOSER_TIMEOUT_MS })
  await sendButton.click()
}

/**
 * Sends an initial chat message and returns the run id accepted by its `run.start`
 * command. This avoids relying on the transient active-run state for runs that can
 * complete or fail before active-run polling begins.
 */
export async function sendMessageForRun(
  page: Page,
  conversationId: string,
  text: string,
): Promise<string> {
  return waitForAcceptedRunStart(page, conversationId, () => sendMessage(page, text))
}

export async function waitForActiveRun(
  request: APIRequestContext,
  conversationId: string,
): Promise<string> {
  let runId = ''
  await expect
    .poll(
      async () => {
        const run = await apiGetJson(
          request,
          `${API_BASE}/api/conversations/${conversationId}/runs/active`,
        )
        if (!isRecord(run)) return null
        runId = stringField(run, 'id', 'active run')
        return typeof run.status === 'string' ? run.status : null
      },
      { timeout: 20_000, intervals: [250, 500, 1000] },
    )
    .toMatch(/queued|running|interrupted/)
  return runId
}

export async function waitForRunStatus(
  request: APIRequestContext,
  conversationId: string,
  runId: string,
  status: string,
): Promise<void> {
  await expect
    .poll(
      async () => {
        const run = await apiGetJson(
          request,
          `${API_BASE}/api/conversations/${conversationId}/runs/${runId}`,
        )
        return isRecord(run) && typeof run.status === 'string' ? run.status : null
      },
      { timeout: 45_000, intervals: [500, 1000, 2000] },
    )
    .toBe(status)
}

export async function expectFinalTextVisible(page: Page, timeout = 60_000): Promise<void> {
  const finalText = page.getByText(FINAL_TEXT).first()
  if (!(await finalText.isVisible())) {
    await page.reload()
  }
  await expect(finalText).toBeVisible({ timeout })
}

export async function waitForArtifact(
  request: APIRequestContext,
  conversationId: string,
  name: string,
): Promise<void> {
  await expect
    .poll(
      async () =>
        records(
          await apiGetJson(request, `${API_BASE}/api/conversations/${conversationId}/artifacts`),
          'artifacts',
        ).some((artifact) => artifact.display_name === name),
      { timeout: ARTIFACT_INDEX_TIMEOUT_MS, intervals: [500, 1000, 2000, 5000] },
    )
    .toBe(true)
}

export async function normalizeArtifactList(page: Page, reportFile: string, notesFile: string) {
  const artifactRail = page.getByRole('complementary')
  const reportArtifactButton = artifactRail
    .getByRole('button', { name: new RegExp(reportFile) })
    .last()
  const notesArtifactButton = artifactRail
    .getByRole('button', { name: new RegExp(notesFile) })
    .last()
  const artifactPreviewHeadings = [
    artifactRail.getByRole('heading', { name: reportFile }),
    artifactRail.getByRole('heading', { name: notesFile }),
  ]
  const artifactListIsVisible = async (): Promise<boolean> =>
    (await Promise.all([reportArtifactButton.isVisible(), notesArtifactButton.isVisible()])).every(
      Boolean,
    )
  const artifactPreviewIsVisible = async (): Promise<boolean> =>
    (await Promise.all(artifactPreviewHeadings.map((heading) => heading.isVisible()))).some(Boolean)

  // 파일 이벤트는 마지막 파일을 자동 미리보기로 열 수 있다. 이벤트가 UI에 반영된 뒤
  // 목록 패널을 선택해야 이후의 파일 선택 계약을 결정적으로 검증할 수 있다.
  if (!(await artifactRail.isVisible())) {
    await page.getByRole('button', { name: /파일 패널|Artifacts/ }).click()
  }
  await expect
    .poll(async () => (await artifactListIsVisible()) || (await artifactPreviewIsVisible()), {
      timeout: 20_000,
      intervals: [250, 500, 1000],
    })
    .toBe(true)
  if (!(await artifactListIsVisible())) {
    await page.getByRole('button', { name: /파일 패널|Artifacts/ }).click()
  }
  await expect(reportArtifactButton).toBeVisible({ timeout: 20_000 })
  await expect(notesArtifactButton).toBeVisible({ timeout: 20_000 })

  return { reportArtifactButton }
}

export async function approveExecuteInSkill(page: Page): Promise<string> {
  await expect(page.getByText(/승인이 필요합니다|Approval Required/).last()).toBeVisible({
    timeout: 30_000,
  })
  await expect
    .poll(async () => page.getByTestId('approval-approve-button').count(), {
      timeout: 10_000,
      intervals: [250, 500, 1000],
    })
    .toBeGreaterThan(0)
  const responsePromise = page.waitForResponse(
    (response) => {
      const request = response.request()
      return (
        request.method() === 'POST' &&
        /\/api\/conversations\/[^/]+\/langgraph\/threads\/[^/]+\/commands$/.test(
          new URL(response.url()).pathname,
        ) &&
        commandMethod(request) === 'input.respond'
      )
    },
    { timeout: 15_000 },
  )
  await expect(async () => {
    const clicked = await page.evaluate(() => {
      const buttons = Array.from(
        document.querySelectorAll<HTMLButtonElement>('[data-testid="approval-approve-button"]'),
      ).filter((button) => button.offsetParent !== null && !button.disabled)
      const button = buttons.at(-1)
      if (!button) return false
      button.click()
      return true
    })
    expect(clicked).toBe(true)
  }).toPass({ timeout: 10_000, intervals: [250, 500, 1000] })
  const response = await Promise.race([
    responsePromise,
    page
      .getByText(
        /승인 응답을 전송하지 못했습니다\. 다시 시도하세요\.|Could not send the approval response\. Try again\./,
      )
      .last()
      .waitFor({ state: 'visible', timeout: 15_000 })
      .then(() => {
        throw new Error('Approval UI rejected the resume before sending input.respond')
      }),
  ])
  const runId = await response.headerValue('X-Run-Id')
  if (!response.ok() || !runId) {
    const body = await response.text().catch(() => '<unavailable>')
    throw new Error(`input.respond was not accepted (${response.status()}): ${body}`)
  }
  return runId
}
