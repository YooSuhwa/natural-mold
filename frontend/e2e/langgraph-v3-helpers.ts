import type { APIRequestContext, Page, Request } from '@playwright/test'
import {
  API_BASE,
  apiGetJson,
  apiPostJson,
  expect,
  isRecord,
  loginApi,
  type CsrfHeaders,
} from './fixtures'

export const DOCX_SKILL_SLUG = 'docx-document'
export const SCRIPTED_PROVIDER = 'e2e_scripted'
export const SCRIPTED_MODEL = 'document-artifact-scripted'
export const FINAL_TEXT = 'E2E LangGraph v3 validation complete'
export const REPORT_FILE = 'moldy-langgraph-v3-report.md'
export const NOTES_FILE = 'moldy-langgraph-v3-notes.txt'

export interface LangGraphV3Setup {
  readonly parentAgentId: string
  readonly childAgentId: string
  readonly childRuntimeName: string
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

function optionalNestedString(
  record: Record<string, unknown>,
  parentKey: string,
  key: string,
): string | null {
  const parent = record[parentKey]
  if (!isRecord(parent)) return null
  const value = parent[key]
  return typeof value === 'string' && value ? value : null
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

function installedSkillId(item: Record<string, unknown>): string | null {
  return optionalNestedString(item, 'installation', 'installed_resource_id')
}

async function installDocxSkill(
  request: APIRequestContext,
  csrfHeaders: CsrfHeaders,
): Promise<string> {
  const item = await docxMarketplaceItem(request)
  const existingId = installedSkillId(item)
  if (existingId) return existingId

  let installed: unknown
  try {
    installed = await apiPostJson(
      request,
      `${API_BASE}/api/marketplace/items/${stringField(item, 'id', 'docx skill')}/install`,
      csrfHeaders,
      { install_mode: 'reuse_or_update' },
    )
  } catch (error) {
    const racedId = installedSkillId(await docxMarketplaceItem(request))
    if (racedId) return racedId
    throw error
  }

  if (!isRecord(installed)) throw new Error('skill install did not return an object')
  const id =
    (typeof installed.installed_skill_id === 'string' ? installed.installed_skill_id : null) ??
    installedSkillId(item)
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
    conversationId: stringField(conversation, 'id', 'conversation'),
    csrfHeaders,
  }
}

export async function sendMessage(page: Page, text: string): Promise<void> {
  const composer = page.locator('textarea[data-moldy-composer-input="true"]').last()
  await expect(composer).toBeVisible()
  await composer.fill(text)
  await composer.press('Enter')
}

export function commandMethod(request: Request): string | null {
  if (request.method() !== 'POST' || !request.url().includes('/langgraph/threads/')) return null
  if (!request.url().endsWith('/commands')) return null
  const raw = request.postData()
  if (!raw) return null
  const parsed: unknown = JSON.parse(raw)
  return isRecord(parsed) && typeof parsed.method === 'string' ? parsed.method : null
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
      { timeout: 45_000, intervals: [500, 1000, 2000] },
    )
    .toBe(true)
}

export async function approveExecuteInSkill(page: Page): Promise<void> {
  await expect(page.getByText(/승인이 필요합니다|Approval Required/).last()).toBeVisible({
    timeout: 30_000,
  })
  await expect
    .poll(async () => page.getByTestId('approval-approve-button').count(), {
      timeout: 10_000,
      intervals: [250, 500, 1000],
    })
    .toBeGreaterThan(0)
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
}
