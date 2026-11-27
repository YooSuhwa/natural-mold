import type { CDPSession, Page } from '@playwright/test'

const answer = 'E2E slow stream completed after detached navigation.'
const prefixes = [
  'E2E slow',
  'E2E slow stream',
  'E2E slow stream completed',
  'E2E slow stream completed after',
  'E2E slow stream completed after detached',
  answer,
]
const uuid = /^[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}$/i

type Kind = 'standard-command' | 'standard-attach' | 'v3-command' | 'v3-attach'
type Stamp = number | null
interface Route {
  kind: Kind
  method: string
  requestAtMs: number
  headersAtMs: Stamp
  publicRunId: string | null
  physicalRunId: string | null
  frameCount: number
}

interface Timing {
  schemaVersion: 1
  mode: 'ag_ui' | 'langgraph_v3' | 'unknown'
  sampleId: string
  conversationId: string
  publicRunId: string | null
  physicalRunId: string | null
  runIds: string[]
  submittedAtMs: Stamp
  firstFrameAtMs: Stamp
  lastFrameAtMs: Stamp
  ordinals: { ordinal: number; networkAtMs: Stamp; domAtMs: Stamp }[]
  routes: Route[]
  parserIssues: number
}

type DomWindow = Window & { __moldyReadSlowAnswer?: () => Stamp[] }
const sessions = new WeakMap<Page, { cdp: CDPSession; timing: Timing }>()

function record(value: unknown): Record<string, unknown> | null {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : null
}

function part(frame: Record<string, unknown>): string | undefined {
  if (frame.type === 'TEXT_MESSAGE_CONTENT' && typeof frame.delta === 'string') return frame.delta
  if (frame.method !== 'messages') return
  const data = record(frame.params)?.data
  const message = record(Array.isArray(data) ? data[0] : data)
  const delta = record(message?.delta)
  if (
    message?.event === 'content-block-delta' &&
    delta?.type === 'text-delta' &&
    typeof delta.text === 'string'
  )
    return delta.text
  if (
    (message?.type === 'AIMessageChunk' || message?.type === 'ai') &&
    typeof message.content === 'string'
  )
    return message.content
}

function captureFrame(timing: Timing, route: Route) {
  let carry = ''
  let prefix = ''
  let reached = 0
  const decoder = new TextDecoder()
  return (bytes: Buffer, at: Stamp) => {
    carry += decoder.decode(bytes, { stream: true })
    if (carry.length > 2_000_000) {
      timing.parserIssues++
      carry = ''
      return
    }
    let separator: RegExpExecArray | null
    while ((separator = /\r?\n\r?\n/.exec(carry))) {
      const frame = carry.slice(0, separator.index)
      carry = carry.slice(separator.index + separator[0].length)
      const data = frame
        .split(/\r?\n/)
        .filter((line) => line.startsWith('data:'))
        .map((line) => line.slice(5).trimStart())
        .join('\n')
      if (
        !data ||
        data === '[DONE]' ||
        timing.submittedAtMs === null ||
        (at !== null && at < timing.submittedAtMs)
      )
        continue
      try {
        const event = record(JSON.parse(data))
        if (!event) continue
        route.frameCount++
        if (route.frameCount === 1 && timing.firstFrameAtMs === null) timing.firstFrameAtMs = at
        timing.lastFrameAtMs = at
        const runId = event.type === 'RUN_STARTED' ? event.runId : undefined
        if (typeof runId === 'string' && uuid.test(runId)) {
          timing.publicRunId ??= runId
          if (!timing.runIds.includes(runId)) timing.runIds.push(runId)
        }
        const delta = part(event)
        if (!delta) continue
        const next = answer.startsWith(prefix + delta) ? prefix + delta : delta
        if (!answer.startsWith(next)) continue
        prefix = next
        while (reached < prefixes.length && prefix.startsWith(prefixes[reached])) {
          if (at === null) timing.parserIssues++
          timing.ordinals[reached++].networkAtMs ??= at
        }
      } catch {
        timing.parserIssues++
      }
    }
  }
}

interface Stream {
  route: Route
  offset: number
  feed: ReturnType<typeof captureFrame>
  awaitingBytes: { at: number; length: number }[]
  earlyBytes: { at: number; data: Buffer }[]
  ready: boolean
}

export async function installStreamPerfTiming(
  page: Page,
  apiBase: string,
  conversationId: string,
  sampleId: string,
): Promise<void> {
  if (!uuid.test(conversationId) || !uuid.test(sampleId))
    throw new Error('Expected isolated fixture UUIDs')
  const cdp = await page.context().newCDPSession(page)
  const timing: Timing = {
    schemaVersion: 1,
    mode: 'unknown',
    sampleId,
    conversationId,
    publicRunId: null,
    physicalRunId: null,
    runIds: [],
    submittedAtMs: null,
    firstFrameAtMs: null,
    lastFrameAtMs: null,
    ordinals: prefixes.map((_, i) => ({ ordinal: i + 1, networkAtMs: null, domAtMs: null })),
    routes: [],
    parserIssues: 0,
  }
  sessions.set(page, { cdp, timing })
  const base = new URL(apiBase)
  const prefix = '/api/conversations/' + conversationId
  const routes = new Map<string, Kind>([
    [prefix + '/ag-ui/runs', 'standard-command'],
    [prefix + '/ag-ui/stream', 'standard-attach'],
    [prefix + '/langgraph/threads/' + conversationId + '/commands', 'v3-command'],
    [prefix + '/langgraph/threads/' + conversationId + '/stream/events', 'v3-attach'],
  ])
  const streams = new Map<string, Stream>()
  cdp.on('Network.requestWillBeSent', (event) => {
    const url = new URL(event.request.url)
    const kind = routes.get(url.pathname)
    if (url.origin !== base.origin || !kind || event.request.method === 'OPTIONS') return
    const route: Route = {
      kind,
      method: event.request.method,
      requestAtMs: event.wallTime * 1000,
      headersAtMs: null,
      publicRunId: null,
      physicalRunId: null,
      frameCount: 0,
    }
    const postData = event.request.postData
    if (
      kind.endsWith('command') &&
      route.method === 'POST' &&
      postData?.includes('E2E_AGUI_PERF_' + sampleId) &&
      (kind === 'standard-command' || /"method"\s*:\s*"run\.start"/.test(postData))
    ) {
      timing.mode = kind === 'standard-command' ? 'ag_ui' : 'langgraph_v3'
      timing.submittedAtMs ??= route.requestAtMs
    }
    timing.routes.push(route)
    streams.set(event.requestId, {
      route,
      offset: (event.wallTime - event.timestamp) * 1000,
      feed: captureFrame(timing, route),
      ready: false,
      earlyBytes: [],
      awaitingBytes: [],
    })
  })
  cdp.on('Network.responseReceived', async (event) => {
    const stream = streams.get(event.requestId)
    if (!stream) return
    const { route, offset } = stream
    route.headersAtMs = event.timestamp * 1000 + offset
    for (const [key, value] of Object.entries(event.response.headers)) {
      const keyLower = key.toLowerCase()
      if (
        typeof value !== 'string' ||
        !uuid.test(value) ||
        (keyLower !== 'x-run-id' && keyLower !== 'x-moldy-run-id')
      )
        continue
      if (keyLower === 'x-moldy-run-id' || route.kind.startsWith('v3-')) {
        route.physicalRunId = value
        timing.physicalRunId ??= value
      }
      if (keyLower === 'x-run-id') {
        route.publicRunId = value
        timing.publicRunId ??= value
      }
      if (!timing.runIds.includes(value)) timing.runIds.push(value)
    }
    if (event.response.mimeType.toLowerCase() !== 'text/event-stream') {
      streams.delete(event.requestId)
      return
    }
    try {
      // CDP reads a native copy. The application's original fetch and stream
      // consumer retain their original buffering, cancellation, and backpressure.
      const { bufferedData } = await cdp.send('Network.streamResourceContent', {
        requestId: event.requestId,
      })
      const buffered = Buffer.from(bufferedData, 'base64')
      let position = 0
      for (const item of stream.awaitingBytes) {
        const end = Math.min(buffered.length, position + item.length)
        if (end > position) stream.feed(buffered.subarray(position, end), item.at)
        position = end
      }
      if (position < buffered.length) {
        // No dataReceived timestamp for the rest of the initial buffer: headers
        // were observed but an exact timestamp for those bytes is unavailable.
        stream.feed(buffered.subarray(position), null)
      }
      for (const item of stream.earlyBytes) stream.feed(item.data, item.at)
      stream.awaitingBytes = []
      stream.earlyBytes = []
      stream.ready = true
    } catch {
      timing.parserIssues++
    }
  })
  cdp.on('Network.dataReceived', (event) => {
    const stream = streams.get(event.requestId)
    if (!stream) return
    const at = event.timestamp * 1000 + stream.offset
    if (event.data) {
      const data = Buffer.from(event.data, 'base64')
      if (stream.ready) stream.feed(data, at)
      else stream.earlyBytes.push({ data, at })
    } else if (!stream.ready && event.dataLength > 0) {
      stream.awaitingBytes.push({ at, length: event.dataLength })
    }
  })
  await cdp.send('Network.enable', { maxPostDataSize: 65536 })
  await page.addInitScript(
    ({ expected, path }) => {
      if (!location.pathname.endsWith(path) || window.top !== window) return
      const seen: (number | null)[] = expected.map(() => null)
      const observer = new MutationObserver(() => {
        const candidates = document.querySelectorAll<HTMLElement>(
          '[data-moldy-message-role="assistant"]',
        )
        const bubble = candidates[candidates.length - 1]
        if (!bubble || bubble.getClientRects().length === 0) return
        const text = bubble.innerText
        const now = performance.timeOrigin + performance.now()
        for (let i = 0; i < seen.length; i++) {
          if (seen[i] === null && text.includes(expected[i])) seen[i] = now
        }
        if (seen[seen.length - 1] !== null) observer.disconnect()
      })
      observer.observe(document, {
        subtree: true,
        childList: true,
        characterData: true,
        attributes: true,
        attributeFilter: ['hidden', 'style', 'class'],
      })
      ;(window as DomWindow).__moldyReadSlowAnswer = () => seen
    },
    { expected: prefixes, path: '/conversations/' + conversationId },
  )
}

export async function readStreamPerfTiming(page: Page): Promise<Timing | null> {
  const session = sessions.get(page)
  if (!session) return null
  const times = await page.evaluate(() => (window as DomWindow).__moldyReadSlowAnswer?.() ?? [])
  for (const slot of session.timing.ordinals) slot.domAtMs = times[slot.ordinal - 1] ?? null
  await session.cdp.detach()
  sessions.delete(page)
  return session.timing
}
