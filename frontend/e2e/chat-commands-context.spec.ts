import type { APIRequestContext, Request } from '@playwright/test'

import {
  API_BASE,
  apiDeleteOk,
  apiGetJson,
  apiJson,
  E2E_PASSWORD,
  expect,
  isRecord,
  test,
} from './fixtures'
import { sendMessage, setupLangGraphV3Agent } from './langgraph-v3-helpers'

function runStartResourceContext(request: Request): readonly unknown[] | null {
  if (request.method() !== 'POST') return null
  const raw = request.postData()
  if (!raw || !raw.includes('"run.start"')) return null
  let body: unknown
  try {
    body = JSON.parse(raw)
  } catch {
    throw new Error('Matched run.start request was not valid JSON')
  }
  if (!isRecord(body) || body.method !== 'run.start') return null
  if (!isRecord(body.params) || !isRecord(body.params.input)) {
    throw new Error('Matched run.start request did not contain params.input')
  }
  const refs = body.params.input.resource_context
  if (refs === undefined) return null
  if (!Array.isArray(refs)) throw new Error('run.start resource_context was not an array')
  return refs
}

async function attachedFileId(
  request: APIRequestContext,
  conversationId: string,
  filename: string,
): Promise<string> {
  let indexedId: string | null = null
  await expect
    .poll(
      async () => {
        const files = await apiGetJson(
          request,
          `${API_BASE}/api/conversations/${conversationId}/files`,
        )
        if (!Array.isArray(files)) throw new Error('Conversation files response was not an array')
        const file = files.find(
          (candidate) =>
            isRecord(candidate) &&
            candidate.source === 'attached' &&
            candidate.name === filename &&
            typeof candidate.id === 'string',
        )
        indexedId = isRecord(file) && typeof file.id === 'string' ? file.id : null
        return indexedId
      },
      { timeout: 15_000, intervals: [250, 500, 1000] },
    )
    .not.toBeNull()
  if (indexedId === null) throw new Error('Attached context file was not indexed')
  return indexedId
}

test.describe('Chat commands and authoritative resource context', () => {
  test.skip(process.env.PW_SKIP_BACKEND === '1', 'Requires the FastAPI backend')
  test.skip(
    process.env.NEXT_PUBLIC_CHAT_RUNTIME === 'legacy',
    'Skipped for the legacy chat runtime',
  )

  test('executes a real command, links a file snapshot, and rejects a non-owner', async ({
    page,
    request,
    playwright,
  }, testInfo) => {
    test.setTimeout(180_000)
    const setup = await setupLangGraphV3Agent(request)
    const foreign = await playwright.request.newContext({ baseURL: API_BASE })
    const filename = `e2e-context-${Date.now()}.txt`
    let foreignAgentId: string | null = null
    let foreignCsrfToken: string | null = null

    try {
      await page.goto(`/agents/${setup.parentAgentId}/conversations/${setup.conversationId}`)
      const composer = page.locator('textarea[data-moldy-composer-input="true"]').last()

      const [chooser] = await Promise.all([
        page.waitForEvent('filechooser'),
        page.getByRole('button', { name: /파일 첨부|Attach file/ }).click(),
      ])
      await chooser.setFiles({
        name: filename,
        mimeType: 'text/plain',
        buffer: Buffer.from('authoritative resource context fixture'),
      })
      await sendMessage(page, 'Index this attachment.')
      await expect(page.getByText('E2E scripted document model is ready.').last()).toBeVisible({
        timeout: 60_000,
      })
      const fileId = await attachedFileId(request, setup.conversationId, filename)

      await composer.fill('/files')
      await composer.press('Enter')
      await expect(page.getByRole('heading', { name: /^(파일|Files)$/, level: 2 })).toBeVisible()

      await page.reload()
      const reloadedComposer = page.locator('textarea[data-moldy-composer-input="true"]').last()
      await reloadedComposer.fill(`@${filename}`)
      const resourceOption = page.getByRole('option', { name: new RegExp(filename) })
      await expect(resourceOption).toBeVisible()
      const hitTest = await resourceOption.evaluate((option) => {
        const bounds = option.getBoundingClientRect()
        const center = { x: bounds.left + bounds.width / 2, y: bounds.top + bounds.height / 2 }
        const hitTarget = document.elementFromPoint(center.x, center.y)
        const ancestors = []
        let current: Element | null = option
        while (current && ancestors.length < 6) {
          const style = getComputedStyle(current)
          const rect = current.getBoundingClientRect()
          ancestors.push({
            height: Math.round(rect.height),
            overflow: style.overflow,
            pointerEvents: style.pointerEvents,
            position: style.position,
            role: current.getAttribute('role'),
            tag: current.tagName.toLowerCase(),
            width: Math.round(rect.width),
            zIndex: style.zIndex,
          })
          current = current.parentElement
        }
        return {
          ancestors,
          hitRole: hitTarget?.getAttribute('role') ?? null,
          hitTag: hitTarget?.tagName.toLowerCase() ?? null,
          ownsHitTarget: option.contains(hitTarget),
        }
      })
      testInfo.annotations.push({
        type: 'moldy.resource-context-hit-test.v1',
        description: JSON.stringify(hitTest),
      })
      expect(hitTest.ownsHitTarget).toBe(true)
      await resourceOption.click()
      await expect(page.getByText(filename).last()).toBeVisible()
      const screenshotPath = testInfo.outputPath('commands-context-selected-file.png')
      await page.screenshot({ path: screenshotPath, fullPage: true })
      await testInfo.attach('commands-context-selected-file', {
        path: screenshotPath,
        contentType: 'image/png',
      })

      const runStart = page.waitForRequest(
        (candidate) => runStartResourceContext(candidate)?.length === 1,
      )
      await sendMessage(page, 'Use the selected file context.')
      const publicRefs = runStartResourceContext(await runStart)
      expect(publicRefs).toEqual([{ kind: 'file', id: fileId, label: filename }])

      await expect
        .poll(async () => {
          const queue = await apiGetJson(
            request,
            `${API_BASE}/api/conversations/${setup.conversationId}/run-inputs`,
          )
          if (!isRecord(queue) || !Array.isArray(queue.items)) return null
          const item = queue.items.find(
            (candidate) =>
              isRecord(candidate) &&
              Array.isArray(candidate.resource_context) &&
              candidate.resource_context.some(
                (reference) => isRecord(reference) && reference.id === fileId,
              ),
          )
          return isRecord(item) ? item.resource_context : null
        })
        .toEqual([{ kind: 'file', id: fileId, label: filename }])

      const registration = await apiJson(
        await foreign.post('/api/auth/register', {
          data: {
            email: `context-foreign-${Date.now()}@moldy.dev`,
            password: E2E_PASSWORD,
            name: 'Other',
          },
        }),
        'foreign registration',
      )
      if (!isRecord(registration) || typeof registration.csrf_token !== 'string') {
        throw new Error('Foreign registration did not return CSRF')
      }
      foreignCsrfToken = registration.csrf_token
      const foreignHeaders = { 'X-CSRF-Token': registration.csrf_token }
      const models = await apiJson(await foreign.get('/api/models'), 'foreign models')
      if (!Array.isArray(models)) throw new Error('Foreign models response was not an array')
      const scriptedModel = models.find(
        (model) =>
          isRecord(model) && model.provider === 'e2e_scripted' && typeof model.id === 'string',
      )
      if (!isRecord(scriptedModel) || typeof scriptedModel.id !== 'string') {
        throw new Error('Foreign user could not resolve the scripted model')
      }
      const foreignAgent = await apiJson(
        await foreign.post('/api/agents', {
          headers: foreignHeaders,
          data: {
            name: `Foreign context owner ${Date.now()}`,
            description: 'Owns the conversation but not the selected file.',
            system_prompt: 'Return a deterministic response.',
            model_id: scriptedModel.id,
            tool_ids: [],
            mcp_tool_ids: [],
            skill_ids: [],
            sub_agent_ids: [],
            middleware_configs: [],
          },
        }),
        'foreign agent',
      )
      if (!isRecord(foreignAgent) || typeof foreignAgent.id !== 'string') {
        throw new Error('Foreign agent create did not return an id')
      }
      foreignAgentId = foreignAgent.id
      const foreignConversation = await apiJson(
        await foreign.post(`/api/agents/${foreignAgentId}/conversations`, {
          headers: foreignHeaders,
          data: { title: 'Foreign owner resource boundary' },
        }),
        'foreign conversation',
      )
      if (!isRecord(foreignConversation) || typeof foreignConversation.id !== 'string') {
        throw new Error('Foreign conversation create did not return an id')
      }
      const denied = await foreign.post(
        `/api/conversations/${foreignConversation.id}/langgraph/threads/${foreignConversation.id}/commands`,
        {
          headers: foreignHeaders,
          data: {
            id: 1,
            method: 'run.start',
            params: {
              assistant_id: foreignAgentId,
              input: {
                messages: [{ role: 'user', content: 'Denied cross-owner context.' }],
                resource_context: [{ kind: 'file', id: fileId }],
              },
            },
          },
        },
      )
      expect(denied.status()).toBe(404)
    } finally {
      if (foreignAgentId && foreignCsrfToken) {
        await foreign.delete(`/api/agents/${foreignAgentId}`, {
          headers: { 'X-CSRF-Token': foreignCsrfToken },
        })
      }
      await foreign.dispose()
      await apiDeleteOk(request, `${API_BASE}/api/agents/${setup.parentAgentId}`, setup.csrfHeaders)
      await apiDeleteOk(request, `${API_BASE}/api/agents/${setup.childAgentId}`, setup.csrfHeaders)
    }
  })
})
