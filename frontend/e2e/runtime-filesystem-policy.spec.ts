import type { APIRequestContext, Page, TestInfo } from '@playwright/test'

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
import { records, sendMessageForRun, stringField, waitForRunStatus } from './langgraph-v3-helpers'

const SCRIPTED_PROVIDER = 'e2e_scripted'
const SCRIPTED_MODEL = 'document-artifact-scripted'
const INSPECT_MARKER = 'E2E_RUNTIME_FILESYSTEM_INSPECT'
const ARTIFACT_WRITE_MARKER = 'E2E_RUNTIME_FILESYSTEM_ARTIFACT_WRITE'
const INSPECT_FINAL = 'E2E runtime inspect policy completed with scoped read-only access.'
const ARTIFACT_FINAL = 'E2E runtime artifact final content.\n'
const ARTIFACT_NAME = 'e2e-runtime-policy-artifact.md'
const FORBIDDEN_TOOL_NAMES = ['execute', 'delete', 'shell', 'execute_in_skill'] as const
const RUNTIME_POLICY_CAPTURE_VIEWPORTS = [375, 768, 1280] as const

interface RuntimePolicyAgentSetup {
  readonly agentId: string
  readonly csrfHeaders: CsrfHeaders
}

interface ArtifactRow {
  readonly id: string
  readonly displayName: string
  readonly status: string
}

async function capturePermissionDeniedState(
  page: Page,
  testInfo: TestInfo,
  deniedTestId = 'filesystem-permission-denied',
  filenamePrefix = 'filesystem-denial',
): Promise<void> {
  if (testInfo.project.name !== 'scripted-capture') return

  for (const width of RUNTIME_POLICY_CAPTURE_VIEWPORTS) {
    await page.setViewportSize({ width, height: 960 })
    const denied = page.getByTestId(deniedTestId)
    await denied.scrollIntoViewIfNeeded()
    await expect(denied).toBeInViewport()
    await page.screenshot({
      path: testInfo.outputPath(`${filenamePrefix}-${width}.png`),
      fullPage: false,
    })
  }
}

test.describe.configure({ mode: 'serial', retries: 0 })

function artifactRows(value: unknown): readonly ArtifactRow[] {
  if (!Array.isArray(value) || !value.every(isRecord)) {
    throw new Error('conversation artifacts did not return records')
  }
  return value.map((artifact) => ({
    id: stringField(artifact, 'id', 'artifact'),
    displayName: stringField(artifact, 'display_name', 'artifact'),
    status: stringField(artifact, 'status', 'artifact'),
  }))
}

async function expectTextVisible(page: Page, text: string): Promise<void> {
  const matches = page.getByText(text)
  await expect
    .poll(
      () =>
        matches.evaluateAll((nodes) =>
          nodes.some((node) => {
            const style = window.getComputedStyle(node)
            return (
              style.display !== 'none' &&
              style.visibility !== 'hidden' &&
              node.getClientRects().length > 0
            )
          }),
        ),
      { timeout: 30_000, intervals: [250, 500, 1000] },
    )
    .toBe(true)
}

function toolCallNames(value: unknown): readonly string[] {
  if (Array.isArray(value)) return value.flatMap(toolCallNames)
  if (!isRecord(value)) return []

  const names: string[] = []
  const rawToolCalls = value.tool_calls
  if (Array.isArray(rawToolCalls)) {
    for (const toolCall of rawToolCalls) {
      if (isRecord(toolCall) && typeof toolCall.name === 'string') {
        names.push(toolCall.name)
      }
    }
  }
  for (const child of Object.values(value)) {
    names.push(...toolCallNames(child))
  }
  return names
}

function runtimePolicy(mode: 'inspect' | 'artifact_write') {
  return {
    version: 1,
    filesystem: { mode },
    todo: { enabled: true },
    summarization: { mode: 'auto' },
  }
}

async function scriptedModelId(request: APIRequestContext): Promise<string> {
  const models = records(await apiGetJson(request, `${API_BASE}/api/models`), 'models')
  const model = models.find(
    (row) => row.provider === SCRIPTED_PROVIDER && row.model_name === SCRIPTED_MODEL,
  )
  if (!model) throw new Error('E2E scripted model is not seeded')
  return stringField(model, 'id', 'scripted model')
}

async function createRuntimePolicyAgent(
  request: APIRequestContext,
  csrfHeaders: CsrfHeaders,
  name: string,
  mode: 'inspect' | 'artifact_write',
  skillIds: readonly string[],
  registerAgent: (agentId: string) => void,
): Promise<RuntimePolicyAgentSetup> {
  const agent = await apiPostJson(request, `${API_BASE}/api/agents`, csrfHeaders, {
    name,
    system_prompt: 'Run only the deterministic E2E runtime filesystem policy sequence.',
    model_id: await scriptedModelId(request),
    tool_ids: [],
    mcp_tool_ids: [],
    skill_ids: [...skillIds],
    sub_agent_ids: [],
    middleware_configs: [],
    runtime_policy: runtimePolicy(mode),
  })
  if (!isRecord(agent)) throw new Error('agent create did not return an object')
  const agentId = stringField(agent, 'id', 'agent')
  registerAgent(agentId)
  expect(agent.runtime_policy_source).toBe('stored')
  expect(agent.runtime_policy).toEqual(runtimePolicy(mode))
  return { agentId, csrfHeaders }
}

async function createConversation(
  request: APIRequestContext,
  setup: RuntimePolicyAgentSetup,
  title: string,
): Promise<string> {
  const conversation = await apiPostJson(
    request,
    `${API_BASE}/api/agents/${setup.agentId}/conversations`,
    setup.csrfHeaders,
    { title },
  )
  if (!isRecord(conversation)) throw new Error('conversation create did not return an object')
  return stringField(conversation, 'id', 'conversation')
}

async function expectStoredRunPolicy(
  request: APIRequestContext,
  conversationId: string,
  runId: string,
): Promise<void> {
  const run = await apiGetJson(
    request,
    `${API_BASE}/api/conversations/${conversationId}/runs/${runId}`,
  )
  if (!isRecord(run)) throw new Error('conversation run did not return an object')
  expect(run.runtime_policy_source).toBe('stored')
  expect(run.runtime_policy_version).toBe(1)
  expect(run.runtime_policy_hash).toMatch(/^[a-f0-9]{64}$/)
}

async function runtimeToolEvidence(
  request: APIRequestContext,
  conversationId: string,
): Promise<unknown> {
  const encodedConversationId = encodeURIComponent(conversationId)
  return apiGetJson(
    request,
    `${API_BASE}/api/conversations/${encodedConversationId}/langgraph/threads/${encodedConversationId}/state`,
  )
}

async function expectSafeToolTrace(
  request: APIRequestContext,
  conversationId: string,
  expectedNames: readonly string[],
): Promise<void> {
  const evidence = await runtimeToolEvidence(request, conversationId)
  const names = toolCallNames(evidence)
  for (const name of expectedNames) expect(names).toContain(name)
  for (const name of FORBIDDEN_TOOL_NAMES) expect(names).not.toContain(name)

  const traceText = JSON.stringify(evidence)
  expect(traceText).not.toContain('/Users/')
  expect(traceText).not.toContain('/private/')
}

function toolResultsForCall(value: unknown, toolCallId: string): readonly string[] {
  if (Array.isArray(value)) return value.flatMap((item) => toolResultsForCall(item, toolCallId))
  if (!isRecord(value)) return []

  const data = value.data
  const results: string[] = []
  if (
    value.event === 'tool_call_result' &&
    isRecord(data) &&
    data.tool_call_id === toolCallId &&
    typeof data.result === 'string'
  ) {
    results.push(data.result)
  }
  if (
    value.type === 'tool' &&
    value.tool_call_id === toolCallId &&
    typeof value.content === 'string'
  ) {
    results.push(value.content)
  }
  for (const child of Object.values(value)) {
    results.push(...toolResultsForCall(child, toolCallId))
  }
  return results
}

async function expectPermissionDeniedToolResult(
  request: APIRequestContext,
  conversationId: string,
  toolCallId: string,
): Promise<void> {
  const evidence = await runtimeToolEvidence(request, conversationId)
  expect(toolResultsForCall(evidence, toolCallId)).toContain('Error: filesystem permission denied')
}

async function expectSafeUnavailableToolResult(
  request: APIRequestContext,
  conversationId: string,
  toolCallId: string,
): Promise<void> {
  const evidence = await runtimeToolEvidence(request, conversationId)
  const results = toolResultsForCall(evidence, toolCallId)
  expect(results.length).toBeGreaterThan(0)
  for (const result of results) {
    expect(result).toMatch(/(?:not a valid tool|unknown tool|unavailable)/i)
    expect(result.length).toBeLessThanOrEqual(1_000)
    expect(result).not.toMatch(/\/(?:Users|private|home|var)\//)
    expect(result).not.toMatch(/(?:api[_-]?key|token|secret)\s*[:=]/i)
  }
}

test.describe('stored runtime filesystem policy E2E', () => {
  test.skip(process.env.PW_SKIP_BACKEND === '1', 'Requires the FastAPI backend')
  test.skip(
    process.env.NEXT_PUBLIC_CHAT_RUNTIME === 'legacy',
    'Skipped for the legacy chat runtime',
  )

  test('reads, lists, and searches the selected skill without creating an artifact in inspect mode', async ({
    page,
    request,
    errors,
  }, testInfo) => {
    test.setTimeout(120_000)
    const unique = Date.now()
    const csrfHeaders = await loginApi(request)
    let skillId: string | null = null
    let agentId: string | null = null

    try {
      const skill = await apiPostJson(request, `${API_BASE}/api/skills`, csrfHeaders, {
        name: `E2E Runtime Inspect Input ${unique}`,
        slug: `e2e-runtime-inspect-${unique}`,
        description: 'Deterministic scoped input for the stored inspect-policy E2E.',
        content: `---\nname: e2e-runtime-inspect-${unique}\ndescription: ${INSPECT_MARKER}\n---\n\n${INSPECT_MARKER}\n`,
      })
      if (!isRecord(skill)) throw new Error('inspect skill create did not return an object')
      skillId = stringField(skill, 'id', 'inspect skill')

      const setup = await createRuntimePolicyAgent(
        request,
        csrfHeaders,
        `E2E Runtime Inspect Agent ${unique}`,
        'inspect',
        [skillId],
        (createdAgentId) => {
          agentId = createdAgentId
        },
      )
      const conversationId = await createConversation(request, setup, 'E2E stored inspect policy')

      await page.goto(`/agents/${agentId}/conversations/${conversationId}`)
      const runId = await sendMessageForRun(page, conversationId, INSPECT_MARKER)
      await waitForRunStatus(request, conversationId, runId, 'completed')
      await expectTextVisible(page, INSPECT_FINAL)
      await expectStoredRunPolicy(request, conversationId, runId)
      await expectSafeToolTrace(request, conversationId, [
        'ls',
        'glob',
        'grep',
        'read_file',
        'write_file',
      ])
      await expectSafeUnavailableToolResult(
        request,
        conversationId,
        'call_e2e_runtime_inspect_write_denied',
      )
      await expectPermissionDeniedToolResult(
        request,
        conversationId,
        'call_e2e_runtime_inspect_sibling_escape',
      )
      await expect(page.getByTestId('filesystem-permission-denied')).toBeVisible()
      await capturePermissionDeniedState(page, testInfo)

      expect(
        artifactRows(
          await apiGetJson(request, `${API_BASE}/api/conversations/${conversationId}/artifacts`),
        ),
      ).toEqual([])
      expect(errors.console).toEqual([])
      expect(errors.network).toEqual([])
    } finally {
      if (agentId) await apiDeleteOk(request, `${API_BASE}/api/agents/${agentId}`, csrfHeaders)
      if (skillId) await apiDeleteOk(request, `${API_BASE}/api/skills/${skillId}`, csrfHeaders)
    }
  })

  test('writes then edits one conversation artifact and reads its final content in artifact_write mode', async ({
    page,
    request,
    errors,
  }, testInfo) => {
    test.setTimeout(120_000)
    const unique = Date.now()
    const csrfHeaders = await loginApi(request)
    let agentId: string | null = null

    try {
      const setup = await createRuntimePolicyAgent(
        request,
        csrfHeaders,
        `E2E Runtime Artifact Agent ${unique}`,
        'artifact_write',
        [],
        (createdAgentId) => {
          agentId = createdAgentId
        },
      )
      const conversationId = await createConversation(
        request,
        setup,
        'E2E stored artifact-write policy',
      )

      await page.goto(`/agents/${agentId}/conversations/${conversationId}`)
      const runId = await sendMessageForRun(page, conversationId, ARTIFACT_WRITE_MARKER)
      await waitForRunStatus(request, conversationId, runId, 'completed')
      await expectTextVisible(page, ARTIFACT_FINAL)
      await expectStoredRunPolicy(request, conversationId, runId)
      await expectSafeToolTrace(request, conversationId, [
        'write_file',
        'edit_file',
        'read_file',
        'ls',
      ])
      await expectPermissionDeniedToolResult(
        request,
        conversationId,
        'call_e2e_runtime_artifact_edit_escape',
      )
      const closeArtifactPreview = page.getByRole('button', { name: 'Close panel' })
      await expect(closeArtifactPreview).toBeVisible()
      await closeArtifactPreview.click()
      await expect(closeArtifactPreview).toBeHidden()
      const editDenial = page.getByTestId('filesystem-edit-denied')
      await expect(editDenial).toBeVisible()
      await expect(page.getByText('tampered', { exact: true })).toHaveCount(0)
      await expect(
        page.getByText('Error: filesystem permission denied', { exact: true }),
      ).toHaveCount(0)
      await capturePermissionDeniedState(
        page,
        testInfo,
        'filesystem-edit-denied',
        'filesystem-edit-denial',
      )
      await expectPermissionDeniedToolResult(
        request,
        conversationId,
        'call_e2e_runtime_artifact_root_escape',
      )

      const artifacts = artifactRows(
        await apiGetJson(request, `${API_BASE}/api/conversations/${conversationId}/artifacts`),
      )
      expect(artifacts).toHaveLength(1)
      const artifact = artifacts[0]
      if (!artifact) throw new Error('artifact list did not include the edited artifact')
      expect(artifact.displayName).toBe(ARTIFACT_NAME)
      expect(artifact.status).toBe('ready')

      const content = await apiGetJson(
        request,
        `${API_BASE}/api/conversations/${conversationId}/artifacts/${artifact.id}/content`,
      )
      if (!isRecord(content)) throw new Error('artifact content did not return an object')
      expect(content.text).toBe(ARTIFACT_FINAL)
      expect(errors.console).toEqual([])
      expect(errors.network).toEqual([])
    } finally {
      if (agentId) await apiDeleteOk(request, `${API_BASE}/api/agents/${agentId}`, csrfHeaders)
    }
  })
})
