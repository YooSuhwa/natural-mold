import { randomUUID } from 'node:crypto'
import { EventType } from '@ag-ui/core'
import type { Page, Request, Response } from '@playwright/test'

import { observeCompletedAgUiResponseReads } from './ag-ui-complete-response-reader'
import { acceptedAgUiStream, agUiCommandThread } from './ag-ui-stream-acceptance'

interface CommandObservation {
  readonly request: Request
  readonly runId: string
  readonly threadId: string
  readonly document: string
  physicalReceipt?: string
  completedPhysical?: string
  cancelledTerminal?: boolean
  stop?: { headersReceived: boolean; matchesRun: boolean }
  invalid: boolean
}

function boundedId(value: unknown): value is string {
  return typeof value === 'string' && value.length <= 512 && value.trim().length > 0
}

function commandKey(threadId: string, runId: string) {
  return JSON.stringify([threadId, runId])
}

/** Confirm native delivery only for the exact request accepted in its original main document. */
export async function installCompletedAgUiResponseObserver(page: Page, apiBase: string) {
  const secret = randomUUID()
  const binding = '__moldyCompleteResponse_' + randomUUID().replaceAll('-', '')
  const documents = new Set<string>()
  const commands = new Map<string, CommandObservation>()
  const byRequest = new WeakMap<Request, CommandObservation>()
  const stops = new WeakMap<Request, CommandObservation>()
  const completed = new WeakSet<Request>()
  let activeDocument: string | undefined
  let disposed = false

  function update(item: CommandObservation) {
    if (
      !item.invalid &&
      item.physicalReceipt &&
      item.physicalReceipt === item.completedPhysical &&
      (!item.cancelledTerminal || item.stop?.matchesRun)
    )
      completed.add(item.request)
    else completed.delete(item.request)
  }

  function onRequest(request: Request) {
    if (
      !activeDocument ||
      request.method() !== 'POST' ||
      request.resourceType() !== 'fetch' ||
      request.redirectedFrom() ||
      request.serviceWorker()
    )
      return
    try {
      if (request.frame() !== page.mainFrame()) return
      const thread = agUiCommandThread(request, apiBase)
      if (!thread) {
        const url = new URL(request.url())
        if (
          url.origin !== new URL(apiBase).origin ||
          url.search ||
          url.hash ||
          url.username ||
          url.password
        )
          return
        const stop = /^\/api\/conversations\/([^/]+)\/runs\/([^/]+)\/cancel$/.exec(url.pathname)
        if (
          !stop ||
          request.postData() ||
          !new Headers(request.headers()).get('X-CSRF-Token')?.trim()
        )
          return
        const owners = [...commands.values()].filter(
          (item) =>
            item.threadId === stop[1] &&
            item.physicalReceipt === stop[2] &&
            !item.invalid &&
            !item.cancelledTerminal &&
            !item.completedPhysical,
        )
        if (owners.length === 1 && !owners[0].stop) {
          const owner = owners[0]
          owner.stop = { headersReceived: false, matchesRun: false }
          stops.set(request, owner)
        }
        return
      }
      const input: unknown = JSON.parse(request.postData() ?? '')
      if (
        !input ||
        typeof input !== 'object' ||
        !('protocolVersion' in input) ||
        input.protocolVersion !== '1.0' ||
        !('threadId' in input) ||
        input.threadId !== thread ||
        !('runId' in input) ||
        !boundedId(input.runId)
      )
        return
      const key = commandKey(thread, input.runId)
      const previous = commands.get(key)
      if (previous) {
        // Idempotent retries can receive identical receipts. Neither request can
        // inherit an EOF reported for the other response.
        previous.invalid = true
        completed.delete(previous.request)
        return
      }
      const item: CommandObservation = {
        request,
        runId: input.runId,
        threadId: thread,
        document: activeDocument,
        invalid: false,
      }
      commands.set(key, item)
      byRequest.set(request, item)
    } catch {
      // Browser workers and malformed command payloads provide no main-frame proof.
    }
  }

  async function verifyStop(response: Response, item: CommandObservation) {
    const stop = item.stop
    if (!stop || item.document !== activeDocument || item.invalid || item.cancelledTerminal) return
    const headers = new Headers(response.headers())
    if (
      response.status() !== 200 ||
      response.fromServiceWorker() ||
      response.url() !== response.request().url() ||
      headers.get('content-type')?.split(';')[0]?.trim().toLowerCase() !== 'application/json'
    )
      return
    stop.headersReceived = true
    try {
      const result: unknown = await response.json()
      if (disposed || item.document !== activeDocument || !result || typeof result !== 'object')
        return
      stop.matchesRun =
        'id' in result &&
        result.id === item.physicalReceipt &&
        'conversation_id' in result &&
        result.conversation_id === item.threadId &&
        'status' in result &&
        (result.status === 'canceling' || result.status === 'canceled')
      update(item)
    } catch {
      // An unreadable receipt cannot prove this worker was stopped.
    }
  }

  function onResponse(response: Response) {
    const stopOwner = stops.get(response.request())
    if (stopOwner) {
      void verifyStop(response, stopOwner)
      return
    }
    const item = byRequest.get(response.request())
    if (!item || item.invalid || item.document !== activeDocument) return
    try {
      if (response.fromServiceWorker() || response.url() !== item.request.url()) {
        item.invalid = true
      } else {
        const receipt = acceptedAgUiStream(
          response.status(),
          new Headers(response.headers()),
          item.runId,
        )
        if (boundedId(receipt.physicalRunId)) item.physicalReceipt = receipt.physicalRunId
        else item.invalid = true
      }
    } catch {
      item.invalid = true
    }
    update(item)
  }

  await page.exposeBinding(binding, (source, value: unknown) => {
    if (
      disposed ||
      source.page !== page ||
      source.frame !== page.mainFrame() ||
      !value ||
      typeof value !== 'object'
    )
      return
    const signal = value as Record<string, unknown>
    if (signal.secret !== secret || !boundedId(signal.document)) return
    if (signal.kind === 'document') {
      if (documents.has(signal.document)) return
      documents.add(signal.document)
      activeDocument = signal.document
      commands.clear()
      return
    }
    if (activeDocument !== signal.document) return
    if (signal.kind === 'closed') {
      activeDocument = undefined
      commands.clear()
      return
    }
    if (
      !['invalid', 'eof', 'cancelled-terminal', 'cancelled-eof'].includes(String(signal.kind)) ||
      !boundedId(signal.threadId) ||
      !boundedId(signal.runId) ||
      !boundedId(signal.physicalRunId)
    )
      return
    const item = commands.get(commandKey(signal.threadId, signal.runId))
    if (!item) return
    if (signal.kind === 'cancelled-terminal') {
      if (
        !item.stop?.headersReceived ||
        item.physicalReceipt !== signal.physicalRunId ||
        item.cancelledTerminal
      )
        item.invalid = true
      item.cancelledTerminal = true
    } else if (
      signal.kind === 'invalid' ||
      (signal.kind === 'cancelled-eof' && !item.cancelledTerminal) ||
      (signal.kind === 'eof' && item.cancelledTerminal)
    )
      item.invalid = true
    else if (item.completedPhysical && item.completedPhysical !== signal.physicalRunId)
      item.invalid = true
    else item.completedPhysical = signal.physicalRunId
    update(item)
  })
  await page.addInitScript(observeCompletedAgUiResponseReads, {
    origin: new URL(apiBase).origin,
    binding,
    secret,
    eventTypes: Object.values(EventType),
  })
  page.on('request', onRequest)
  page.on('response', onResponse)
  return {
    hasCompleted: (request: Request) => !disposed && completed.has(request),
    dispose() {
      disposed = true
      commands.clear()
      documents.clear()
      page.off('request', onRequest)
      page.off('response', onResponse)
    },
  }
}
