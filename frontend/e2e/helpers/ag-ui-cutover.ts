import { execFileSync } from 'node:child_process'
import type { APIRequestContext, Browser, BrowserContext, Page } from '@playwright/test'
import { z } from 'zod'

import {
  ONBOARDING_DISMISSED_FLAG,
  SUPER_USER_WELCOMED_FLAG,
} from '../../src/lib/auth/session-flags'
import { API_BASE, apiGetJson, expect } from '../fixtures'
import { verifyAgUiTranscript } from './ag-ui-sdk-verification'

export function requiredEnvironment(name: string): string {
  const value = process.env[name]
  if (!value) throw new Error(`The AG-UI cutover lane requires ${name}`)
  return value
}

export async function openReplica(browser: Browser, owner: BrowserContext | null) {
  const context = await browser.newContext({
    storageState: owner ? await owner.storageState() : { cookies: [], origins: [] },
    extraHTTPHeaders: { 'x-moldy-e2e-worker': 'b' },
  })
  await context.addInitScript(
    (keys) => {
      if (window.top === window) keys.forEach((key) => sessionStorage.setItem(key, '1'))
    },
    [ONBOARDING_DISMISSED_FLAG, SUPER_USER_WELCOMED_FLAG],
  )
  return context
}

const pendingRequest = z.object({
  protocolVersion: z.string(),
  threadId: z.string(),
  runId: z.string().min(1),
  resume: z.array(z.object({ interruptId: z.string(), status: z.string() })).nullish(),
})

export function standardPath(conversationId: string) {
  return `/api/conversations/${conversationId}/ag-ui/runs`
}

interface BrowserTranscript {
  text: string
  finished: boolean
}

declare global {
  interface Window {
    moldyCutoverTranscripts?: Map<string, BrowserTranscript>
  }
}

/** Chromium can discard a streamed response before Playwright reads it. Observe the same fetch. */
export async function observeStandardStreams(page: Page, conversationId: string) {
  await page.addInitScript((path) => {
    const recorded = new Map<string, BrowserTranscript>()
    window.moldyCutoverTranscripts = recorded
    const fetch = window.fetch.bind(window)
    window.fetch = async (input, init) => {
      const response = await fetch(input, init)
      const method = init?.method ?? (input instanceof Request ? input.method : 'GET')
      if (method !== 'POST' || !response.url || new URL(response.url).pathname !== path)
        return response
      const id = response.headers.get('X-Run-Id')
      if (
        !id ||
        !response.ok ||
        !response.headers.get('content-type')?.includes('text/event-stream')
      )
        return response
      const reader = response.clone().body?.getReader()
      if (!reader) return response
      const transcript = { text: '', finished: false }
      recorded.set(id, transcript)
      void (async () => {
        const decoder = new TextDecoder()
        try {
          while (true) {
            const { done, value } = await reader.read()
            if (done) break
            transcript.text += decoder.decode(value, { stream: true })
          }
        } catch {
          // An SDK subscriber may detach immediately after a terminal event. Its preserved frames
          // still have to pass the full SDK lifecycle validator, which rejects truncation.
        } finally {
          transcript.text += decoder.decode()
          transcript.finished = true
          reader.releaseLock()
        }
      })()
      return response
    }
  }, standardPath(conversationId))
}

export function observeMutations(page: Page, conversationId: string) {
  const mutations: string[] = []
  page.on('request', (request) => {
    if (request.method() !== 'POST') return
    const pathname = new URL(request.url()).pathname
    if (!pathname.startsWith(`/api/conversations/${conversationId}/`)) return
    if (pathname === standardPath(conversationId)) mutations.push('ag-ui')
    else if (pathname.endsWith('/commands')) {
      const body = z.object({ method: z.string() }).safeParse(request.postDataJSON())
      if (body.success && ['run.start', 'run.cancel', 'input.respond'].includes(body.data.method)) {
        mutations.push(body.data.method)
      }
    } else if (pathname.endsWith('/cancel')) {
      mutations.push('cancel')
    }
  })
  return mutations
}

export async function standardCommand(
  page: Page,
  conversationId: string,
  action: () => Promise<void>,
  outcome: 'success' | 'interrupt',
  assistantText?: string,
  inspectInterrupts?: (slots: readonly unknown[]) => void,
) {
  const [response] = await Promise.all([
    page.waitForResponse(
      (res) =>
        res.request().method() === 'POST' &&
        new URL(res.url()).pathname === standardPath(conversationId),
      { timeout: 60_000 },
    ),
    action(),
  ])
  expect(response.status()).toBe(200)
  expect(await response.headerValue('content-type')).toContain('text/event-stream')
  const body = pendingRequest.parse(response.request().postDataJSON())
  expect(body.threadId).toBe(conversationId)
  expect(body.protocolVersion).toBe('1.0')
  expect(await response.headerValue('x-run-id')).toBe(body.runId)
  const transcript = await page.waitForFunction(
    (id) => {
      const found = window.moldyCutoverTranscripts?.get(id)
      return found?.finished ? found.text : undefined
    },
    body.runId,
    { timeout: 90_000 },
  )
  const verified = await verifyAgUiTranscript({
    threadId: conversationId,
    runId: body.runId,
    outcome,
    sse: z.string().parse(await transcript.jsonValue()),
    assistantText,
    inspectInterrupts,
  })
  return { body, verified }
}

export function snapshotResponse(page: Page, conversationId: string, replica: 'a' | 'b') {
  const path = `/api/conversations/${conversationId}/ag-ui/snapshot`
  return page.waitForResponse(
    async (response) =>
      response.request().method() === 'GET' &&
      new URL(response.url()).pathname === path &&
      response.status() === 200 &&
      (await response.headerValue('x-moldy-e2e-worker')) === replica,
    { timeout: 60_000 },
  )
}

export async function company(request: APIRequestContext) {
  const organizations = z
    .array(z.object({ is_active: z.boolean(), id: z.string(), tenant_id: z.string() }))
    .parse(await apiGetJson(request, `${API_BASE}/api/me/organizations`))
  const active = organizations.find((row) => row.is_active)
  if (!active) throw new Error('Missing active organization for the browser session')
  return active
}

const observed = z.object({
  conversation_id: z.string(),
  runs: z.array(
    z.object({ id: z.string(), status: z.string(), parent_run_id: z.string().nullable() }),
  ),
  inputs: z.array(z.object({ id: z.string(), status: z.string(), run_id: z.string().nullable() })),
  counts: z.object({ runs: z.number(), inputs: z.number() }),
  skill_uses: z.array(z.object({ id: z.string(), execution_count: z.number() })),
})

export function databaseState(conversationId: string) {
  const output = execFileSync(
    requiredEnvironment('MOLDY_GATE_PYTHON'),
    [requiredEnvironment('MOLDY_AGUI_OBSERVER'), conversationId],
    { encoding: 'utf8' },
  )
  const data = observed.parse(JSON.parse(output))
  expect(data.conversation_id).toBe(conversationId)
  data.runs.sort((a, b) => a.id.localeCompare(b.id))
  data.inputs.sort((a, b) => a.id.localeCompare(b.id))
  const skillExecutions = data.skill_uses.reduce((sum, row) => sum + row.execution_count, 0)
  return { ...data, skillExecutions }
}

export async function savedMessages(request: APIRequestContext, conversationId: string) {
  const payload = await apiGetJson(
    request,
    `${API_BASE}/api/conversations/${conversationId}/messages`,
  )
  return z
    .object({
      messages: z.array(
        z.object({
          id: z.string(),
          role: z.string(),
          content: z.string(),
          runtime_message_id: z.string().nullish(),
          branch_checkpoint_id: z.string().nullish(),
          feedback: z.object({ rating: z.string() }).nullish(),
        }),
      ),
    })
    .parse(payload).messages
}
