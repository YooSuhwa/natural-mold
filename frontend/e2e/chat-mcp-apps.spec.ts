import type { APIRequestContext, Response } from '@playwright/test'
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
import { startMcpAppsFixture, type McpAppsFixture } from './helpers/mcp-apps-fixture'
import {
  unexpectedMcpAppsBrowserErrors,
  type ConsoleErrorObservation,
  type RequestFailureObservation,
} from './helpers/mcp-apps-network-errors'

const RESOURCE_URI = 'ui://weather/dashboard'

interface SeededMcpApp {
  readonly agentId: string
  readonly conversationId: string
  readonly runId: string
  readonly mcpServerId: string
  readonly toolCallId: string
  readonly artifact: Record<string, unknown>
}

function requiredString(value: Record<string, unknown>, key: string): string {
  const field = value[key]
  if (typeof field !== 'string' || field.length === 0) {
    throw new Error(`MCP Apps fixture response is missing ${key}`)
  }
  return field
}

async function firstModelId(request: APIRequestContext): Promise<string> {
  const body = await apiGetJson(request, `${API_BASE}/api/models`)
  if (!Array.isArray(body) || !isRecord(body[0])) {
    throw new Error('MCP Apps E2E requires one configured model')
  }
  return requiredString(body[0], 'id')
}

async function createAgent(request: APIRequestContext, csrfHeaders: CsrfHeaders): Promise<string> {
  const body = await apiPostJson(request, `${API_BASE}/api/agents`, csrfHeaders, {
    name: `E2E MCP Apps ${Date.now()}`,
    system_prompt: 'Exercise the server-owned MCP Apps fixture.',
    model_id: await firstModelId(request),
  })
  if (!isRecord(body)) throw new Error('Agent creation did not return an object')
  return requiredString(body, 'id')
}

async function createConversation(
  request: APIRequestContext,
  csrfHeaders: CsrfHeaders,
  agentId: string,
): Promise<string> {
  const body = await apiPostJson(
    request,
    `${API_BASE}/api/agents/${agentId}/conversations`,
    csrfHeaders,
    { title: 'MCP App persisted binding' },
  )
  if (!isRecord(body)) throw new Error('Conversation creation did not return an object')
  return requiredString(body, 'id')
}

async function seedMcpApp(
  request: APIRequestContext,
  csrfHeaders: CsrfHeaders,
  conversationId: string,
  serverUrl: string,
): Promise<SeededMcpApp> {
  const body = await apiPostJson(
    request,
    `${API_BASE}/api/e2e/conversations/${conversationId}/mcp-apps/fixture`,
    csrfHeaders,
    { server_url: serverUrl },
  )
  if (!isRecord(body) || !isRecord(body.artifact)) {
    throw new Error('MCP Apps seed helper did not return an artifact')
  }
  return {
    agentId: requiredString(body, 'agent_id'),
    conversationId: requiredString(body, 'conversation_id'),
    runId: requiredString(body, 'run_id'),
    mcpServerId: requiredString(body, 'mcp_server_id'),
    toolCallId: requiredString(body, 'tool_call_id'),
    artifact: body.artifact,
  }
}

function isToolCallProxyResponse(response: Response, seeded: SeededMcpApp): boolean {
  if (
    response.request().method() !== 'POST' ||
    !response
      .url()
      .endsWith(
        `/api/conversations/${seeded.conversationId}/runs/${seeded.runId}/mcp-apps/${seeded.toolCallId}`,
      )
  ) {
    return false
  }
  const body: unknown = response.request().postDataJSON()
  return isRecord(body) && body.method === 'tools/call'
}

test.describe('Chat MCP Apps', () => {
  test.skip(process.env.PW_SKIP_BACKEND === '1', 'Requires the FastAPI backend')
  test.skip(
    process.env.E2E_TEST_HELPERS_ENABLED !== 'true',
    'Requires E2E_TEST_HELPERS_ENABLED=true for a real MCP invocation binding',
  )

  test('rehydrates a real bound app and proxies its widget tool call', async ({
    page,
    request,
    errors,
  }, testInfo) => {
    test.setTimeout(60_000)
    const consoleErrors: ConsoleErrorObservation[] = []
    const requestFailures: RequestFailureObservation[] = []
    page.on('console', (message) => {
      if (message.type() !== 'error') return
      consoleErrors.push({ text: message.text(), locationUrl: message.location().url })
    })
    page.on('requestfailed', (request_) => {
      let frameUrl: string | null = null
      try {
        frameUrl = request_.frame().url()
      } catch {
        // Requests without a frame remain observable and fail classification below.
      }
      requestFailures.push({
        url: request_.url(),
        errorText: request_.failure()?.errorText ?? null,
        resourceType: request_.resourceType(),
        frameUrl,
      })
    })
    const csrfHeaders = await loginApi(request)
    const agentId = await createAgent(request, csrfHeaders)
    let fixture: McpAppsFixture | null = null
    let seeded: SeededMcpApp | null = null

    try {
      const conversationId = await createConversation(request, csrfHeaders, agentId)
      fixture = await startMcpAppsFixture()
      const appSeed = await seedMcpApp(request, csrfHeaders, conversationId, fixture.url)
      seeded = appSeed

      expect(appSeed.agentId).toBe(agentId)
      expect(appSeed.conversationId).toBe(conversationId)
      const mcpApp = appSeed.artifact.mcp_app
      expect(isRecord(mcpApp)).toBe(true)
      if (!isRecord(mcpApp)) throw new Error('Seeded artifact is missing mcp_app')
      expect(mcpApp.run_id).toBe(appSeed.runId)
      expect(mcpApp.resource_uri).toBe(RESOURCE_URI)
      expect(mcpApp.binding_id).toMatch(/^[0-9a-f-]{36}$/i)
      expect(JSON.stringify(appSeed.artifact)).not.toContain('Authorization')
      expect(JSON.stringify(appSeed.artifact)).not.toContain(fixture.url)

      await page.goto(`/agents/${appSeed.agentId}/conversations/${appSeed.conversationId}`)
      const app = page.locator(`[data-mcp-app-resource="${RESOURCE_URI}"]`)
      await expect(app).toBeVisible({ timeout: 20_000 })

      const appFrame = app.locator('iframe')
      await expect(appFrame).toHaveCount(1, { timeout: 20_000 })
      const frame = appFrame.contentFrame()
      await expect(frame.getByText('Weather: 23 C')).toBeVisible({ timeout: 20_000 })
      const [toolResponse] = await Promise.all([
        page.waitForResponse((response) => isToolCallProxyResponse(response, appSeed)),
        frame.getByRole('button', { name: 'Refresh weather' }).click(),
      ])

      expect(toolResponse.ok()).toBe(true)
      const posted: unknown = toolResponse.request().postDataJSON()
      expect(posted).toEqual({
        method: 'tools/call',
        params: { name: 'refresh_weather', arguments: { city: 'Seoul' } },
      })
      expect(JSON.stringify(posted)).not.toContain('serverId')
      expect(JSON.stringify(posted)).not.toContain('binding_id')
      await expect(frame.getByText('Weather refreshed')).toBeVisible()
      const screenshotPath = testInfo.outputPath('mcp-apps-refreshed.png')
      await page.screenshot({ path: screenshotPath, fullPage: true })
      await testInfo.attach('mcp-apps-refreshed', {
        path: screenshotPath,
        contentType: 'image/png',
      })
      const unexpectedErrors = unexpectedMcpAppsBrowserErrors(
        consoleErrors,
        requestFailures,
        new URL(page.url()).origin,
      )
      testInfo.annotations.push({
        type: 'moldy.mcp-apps-shim-telemetry.v1',
        description: JSON.stringify({ consoleErrors, requestFailures }),
      })
      expect(unexpectedErrors).toEqual({ consoleErrors: [], requestFailures: [] })
      expect(errors.console).toEqual(consoleErrors.map((error) => error.text))
      expect(errors.page).toEqual([])
      expect(errors.network).toEqual(requestFailures.length === 0 ? [] : ['other_request_failure'])
    } finally {
      await fixture?.stop()
      if (seeded) {
        await apiDeleteOk(request, `${API_BASE}/api/mcp-servers/${seeded.mcpServerId}`, csrfHeaders)
      }
      await apiDeleteOk(request, `${API_BASE}/api/agents/${agentId}`, csrfHeaders)
    }
  })
})
