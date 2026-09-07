import type { APIRequestContext, Page, Request, Response } from '@playwright/test'

import { API_BASE, apiGetJson, expect, isRecord } from './fixtures'
import { sendMessage } from './langgraph-v3-helpers'

export type QueueItem = Record<string, unknown> & {
  readonly id: string
  readonly status: string
  readonly revision: number
  readonly run_id: string | null
  readonly client_request_id: string
}

type QueueState = Readonly<{
  paused: boolean
  items: readonly QueueItem[]
}>

type QueueSubmission = Readonly<{
  inputId: string
  runId: string | null
}>

type PersistedEnqueue = Readonly<{
  inputId: string
  responsePromise: Promise<Response>
}>

function textFromMessageContent(content: unknown): string {
  if (typeof content === 'string') return content
  if (!Array.isArray(content)) return ''
  return content
    .map((part) => {
      if (typeof part === 'string') return part
      return isRecord(part) && typeof part.text === 'string' ? part.text : ''
    })
    .filter(Boolean)
    .join('\n')
}

function messageRole(message: Record<string, unknown>): 'human' | 'assistant' | null {
  const role = typeof message.role === 'string' ? message.role : message.type
  if (role === 'user' || role === 'human') return 'human'
  if (role === 'assistant' || role === 'ai') return 'assistant'
  return null
}

export function inputHasExactHuman(value: unknown, expectedText: string): boolean {
  if (!isRecord(value) || !Array.isArray(value.messages)) return false
  return value.messages.filter(isRecord).some((message) => {
    const role = messageRole(message)
    return role === 'human' && textFromMessageContent(message.content) === expectedText
  })
}

export function runStartHasExactHuman(request: Request | undefined, expectedText: string): boolean {
  if (!request) return false
  try {
    const body = request.postDataJSON() as { params?: unknown }
    return isRecord(body.params) && inputHasExactHuman(body.params.input, expectedText)
  } catch {
    return false
  }
}

export async function expectUniqueTranscriptTurn(
  page: Page,
  expectedHuman: string,
  expectedAssistant: string,
): Promise<void> {
  const exactHumanText = page.getByText(expectedHuman, { exact: true })
  const userMessage = page
    .locator('[data-moldy-message-role="user"]')
    .filter({ has: exactHumanText })
  await expect(userMessage).toHaveCount(1)
  const userBubble = userMessage.locator('.moldy-chat-bubble-user')
  await expect(userBubble).toHaveCount(1)
  await expect(userBubble).toHaveText(expectedHuman)
  await expect(userBubble).toBeVisible()

  const exactAssistantText = page.getByText(expectedAssistant, { exact: true })
  const assistantMessage = page
    .locator('[data-moldy-message-role="assistant"]')
    .filter({ has: exactAssistantText })
  await expect(assistantMessage).toHaveCount(1)
  await expect(assistantMessage).toBeVisible()
  await expect(assistantMessage.getByText(expectedAssistant, { exact: true })).toBeVisible()
}

export function commandStrategy(request: Request): string | null {
  if (request.method() !== 'POST') return null
  try {
    const body = request.postDataJSON() as { method?: unknown; params?: unknown }
    if (body.method !== 'run.start' || !isRecord(body.params)) return null
    const strategy = body.params.multitask_strategy
    return typeof strategy === 'string' ? strategy : null
  } catch {
    return null
  }
}

export async function queueState(
  request: APIRequestContext,
  conversationId: string,
): Promise<QueueState> {
  const value = await apiGetJson(
    request,
    `${API_BASE}/api/conversations/${conversationId}/run-inputs`,
  )
  if (!isRecord(value) || !Array.isArray(value.items)) {
    throw new Error('queue state did not return an item list')
  }
  return {
    paused: value.queue_paused === true,
    items: value.items.filter(isRecord) as QueueItem[],
  }
}

export async function sendForStrategy(
  page: Page,
  text: string,
  strategy: 'enqueue' | 'interrupt',
): Promise<QueueSubmission> {
  const responsePromise = page.waitForResponse(
    (response) => commandStrategy(response.request()) === strategy,
  )
  if (strategy === 'enqueue') {
    await sendMessage(page, text)
  } else {
    const composer = page.locator('textarea[data-moldy-composer-input="true"]').last()
    await composer.fill(text)
    const steerButton = page.locator('[data-moldy-queue-steer-new]')
    await expect(steerButton).toBeVisible({ timeout: 10_000 })
    await expect(steerButton).toBeEnabled({ timeout: 10_000 })
    await steerButton.click()
  }
  const response = await responsePromise
  expect(response.ok()).toBeTruthy()
  const body = (await response.json()) as { result?: unknown }
  if (!isRecord(body.result) || typeof body.result.input_id !== 'string') {
    throw new Error('queued run.start did not return input_id')
  }
  return {
    inputId: body.result.input_id,
    runId: typeof body.result.run_id === 'string' ? body.result.run_id : null,
  }
}

export async function beginPersistedEnqueue(
  page: Page,
  request: APIRequestContext,
  conversationId: string,
  text: string,
): Promise<PersistedEnqueue> {
  const responsePromise = page.waitForResponse(
    (response) => commandStrategy(response.request()) === 'enqueue',
  )
  const requestPromise = page.waitForRequest(
    (candidate) => commandStrategy(candidate) === 'enqueue',
  )
  await sendMessage(page, text)
  const command = await requestPromise
  const body = command.postDataJSON() as { params?: unknown }
  if (!isRecord(body.params) || typeof body.params.client_request_id !== 'string') {
    throw new Error('queued run.start did not send client_request_id')
  }
  const requestId = body.params.client_request_id
  let inputId = ''
  await expect
    .poll(async () => {
      const input = (await queueState(request, conversationId)).items.find(
        (candidate) => candidate.client_request_id === requestId,
      )
      inputId = input?.id ?? ''
      return input?.status
    })
    .toBe('pending')
  return { inputId, responsePromise }
}
