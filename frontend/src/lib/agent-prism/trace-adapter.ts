import type {
  LangfuseDocument,
  LangfuseObservation,
  LangfuseObservationLevel,
  LangfuseObservationType,
  TraceRecord,
  TraceSpan,
  TraceSpanStatus,
} from '@evilmartians/agent-prism-types'
import { langfuseSpanAdapter } from '@evilmartians/agent-prism-data'

import type {
  DebugTraceDetailResponse,
  DebugTraceSpan,
  DebugTraceSummary,
} from '@/lib/types'
import type { TraceViewerData } from '@/components/agent-prism/TraceViewer/TraceViewer'

const DEFAULT_PROJECT_ID = 'moldy'
const DEFAULT_ENVIRONMENT = 'moldy'

export function toAgentPrismTraceViewerData(
  traces: DebugTraceSummary[],
  details: Array<DebugTraceDetailResponse | undefined>,
): TraceViewerData[] {
  return traces.map((trace) => {
    const detail = details.find((item) => item?.trace.trace_id === trace.trace_id)
    const normalizedTrace = detail?.trace ?? trace
    const observations = observationsForTrace(normalizedTrace, detail)
    const statusById = new Map(
      observations.map((observation) => [observation.id, statusFromObservation(observation)]),
    )
    const spans = langfuseSpanAdapter.convertRawDocumentsToSpans({
      trace: traceToLangfuseTrace(normalizedTrace),
      observations,
    })

    return {
      traceRecord: traceToRecord(normalizedTrace, spans),
      spans: applyStatuses(spans, statusById),
      badges: [
        { label: normalizedTrace.provider },
        ...(normalizedTrace.fallback ? [{ label: 'fallback' }] : []),
      ],
      spanCardViewOptions: {
        withStatus: true,
        expandButton: 'inside',
      },
    }
  })
}

function observationsForTrace(
  trace: DebugTraceSummary,
  detail: DebugTraceDetailResponse | undefined,
): LangfuseObservation[] {
  const spans = detail?.spans?.length
    ? hideDuplicateTraceRootSpans(detail.spans, trace)
    : [rootSpanFromTrace(trace)]
  return spans.map((span, index) => spanToObservation(span, trace, index))
}

function hideDuplicateTraceRootSpans(
  spans: DebugTraceSpan[],
  trace: DebugTraceSummary,
): DebugTraceSpan[] {
  const rootSpans = spans.filter((span) => span.parent_id == null)
  const hasRuntimeRoot = rootSpans.some(
    (span) => span.kind !== 'span' && span.name !== trace.name,
  )
  if (!hasRuntimeRoot) return spans
  return spans.filter((span) => {
    const isTraceShellRoot =
      span.parent_id == null &&
      span.kind === 'span' &&
      span.name === trace.name &&
      span.output == null
    return !isTraceShellRoot
  })
}

function traceToRecord(trace: DebugTraceSummary, spans: TraceSpan[]): TraceRecord {
  return {
    id: trace.trace_id,
    name: trace.name,
    spansCount: countSpans(spans),
    durationMs: trace.duration_ms ?? 0,
    agentDescription: trace.source ?? trace.provider,
    totalTokens: trace.total_tokens ?? undefined,
    startTime: new Date(trace.started_at).getTime(),
  }
}

function traceToLangfuseTrace(trace: DebugTraceSummary): LangfuseDocument['trace'] {
  return {
    id: trace.trace_id,
    projectId: DEFAULT_PROJECT_ID,
    name: trace.name,
    timestamp: trace.started_at,
    environment: DEFAULT_ENVIRONMENT,
    tags: ['moldy', trace.source ?? 'chat', trace.provider],
    bookmarked: false,
    release: null,
    version: null,
    sessionId: trace.moldy_run_id,
    public: false,
    input: null,
    output: null,
    metadata: {
      moldy_run_id: trace.moldy_run_id,
      source: trace.source,
      provider: trace.provider,
      fallback: trace.fallback,
      fallback_reason: trace.fallback_reason,
    },
    createdAt: trace.started_at,
    updatedAt: trace.completed_at ?? trace.started_at,
    scores: [],
    latency: trace.duration_ms ? trace.duration_ms / 1000 : undefined,
  }
}

function spanToObservation(
  span: DebugTraceSpan,
  trace: DebugTraceSummary,
  index: number,
): LangfuseObservation {
  const startTime = span.started_at ?? trace.started_at
  const endTime = span.ended_at ?? trace.completed_at ?? startTime
  const metadata = {
    ...span.metadata,
    moldy_kind: span.kind,
    moldy_status: span.status,
    duration_ms: span.duration_ms,
  }

  return {
    id: span.id || `span:${index + 1}`,
    traceId: trace.trace_id,
    projectId: DEFAULT_PROJECT_ID,
    environment: DEFAULT_ENVIRONMENT,
    parentObservationId: span.parent_id,
    startTime,
    endTime,
    name: span.name,
    metadata: metadataForAgentPrism(metadata),
    type: typeFromKind(span.kind),
    level: levelFromStatus(span.status),
    input: formatIOForAgentPrism(span.input, 'input'),
    output: formatIOForAgentPrism(span.output, 'output'),
    statusMessage: span.status === 'failed' || span.kind === 'error' ? span.name : null,
    createdAt: startTime,
    updatedAt: endTime,
    usageDetails: usageFromMetadata(span, trace, index === 0),
    costDetails: costFromMetadata(span),
  }
}

function rootSpanFromTrace(trace: DebugTraceSummary): DebugTraceSpan {
  return {
    id: `${trace.trace_id}:root`,
    parent_id: null,
    name: trace.name,
    kind: 'workflow',
    status: trace.status,
    started_at: trace.started_at,
    ended_at: trace.completed_at,
    duration_ms: trace.duration_ms,
    input: null,
    output: trace.fallback_reason ? { fallback_reason: trace.fallback_reason } : null,
    metadata: {
      moldy_run_id: trace.moldy_run_id,
      provider: trace.provider,
      langfuse_url: trace.langfuse_url,
    },
  }
}

function applyStatuses(spans: TraceSpan[], statusById: Map<string, TraceSpanStatus>): TraceSpan[] {
  return spans.map((span) => ({
    ...span,
    status: statusById.get(span.id) ?? span.status,
    children: span.children ? applyStatuses(span.children, statusById) : undefined,
  }))
}

function statusFromObservation(observation: LangfuseObservation): TraceSpanStatus {
  if (observation.level === 'ERROR') return 'error'
  if (observation.level === 'WARNING') return 'warning'
  return 'success'
}

function levelFromStatus(status: string | null | undefined): LangfuseObservationLevel {
  const normalized = status?.toLowerCase()
  if (normalized === 'failed' || normalized === 'error') return 'ERROR'
  if (normalized === 'warning') return 'WARNING'
  return 'DEFAULT'
}

function typeFromKind(kind: string): LangfuseObservationType {
  switch (kind) {
    case 'workflow':
      return 'CHAIN'
    case 'llm':
      return 'GENERATION'
    case 'tool':
    case 'skill':
      return 'TOOL'
    case 'error':
    case 'event':
      return 'EVENT'
    default:
      return 'SPAN'
  }
}

function usageFromMetadata(
  span: DebugTraceSpan,
  trace: DebugTraceSummary,
  root: boolean,
): LangfuseObservation['usageDetails'] {
  const usage = objectValue(span.metadata.usage)
  if (usage) return usage
  if (!root || !trace.total_tokens) return undefined
  return { total: trace.total_tokens }
}

function costFromMetadata(span: DebugTraceSpan): LangfuseObservation['costDetails'] {
  const usage = objectValue(span.metadata.usage)
  const estimated = numberValue(usage?.estimated_cost)
  return estimated == null ? undefined : { total: estimated }
}

function metadataForAgentPrism(value: unknown): string {
  const metadata = objectValue(value) ?? {}
  return JSON.stringify({ attributes: metadata })
}

function formatIOForAgentPrism(value: unknown, section: 'input' | 'output'): string | null {
  if (value == null) return null
  const messageText = messageTextForSection(value, section)
  if (messageText) return messageText
  if (typeof value === 'string') {
    const parsed = parseJsonString(value)
    if (parsed !== undefined) {
      const parsedMessageText = messageTextForSection(parsed, section)
      if (parsedMessageText) return parsedMessageText
    }
    return value
  }
  return JSON.stringify(value, null, 2)
}

function messageTextForSection(value: unknown, section: 'input' | 'output'): string | null {
  const messages = extractMessages(value)
  if (!messages.length) return null
  const preferredRoles =
    section === 'input' ? new Set(['human', 'user']) : new Set(['ai', 'assistant'])
  const preferred = [...messages].reverse().find((message) => {
    const role = messageRole(message)
    return role ? preferredRoles.has(role) : false
  })
  const fallback = [...messages].reverse().find((message) => contentText(message.content))
  return contentText((preferred ?? fallback)?.content) ?? null
}

type MessageLike = {
  role?: unknown
  type?: unknown
  content?: unknown
  update?: unknown
  messages?: unknown
}

function extractMessages(value: unknown): MessageLike[] {
  if (Array.isArray(value)) {
    return value.flatMap((item) => extractMessages(item))
  }
  if (!isRecord(value)) return []
  if (Array.isArray(value.messages)) {
    return value.messages.flatMap((item) => extractMessages(item))
  }
  if (isRecord(value.update) && Array.isArray(value.update.messages)) {
    return value.update.messages.flatMap((item) => extractMessages(item))
  }
  if ('content' in value) {
    return [value]
  }
  return []
}

function messageRole(message: MessageLike): string | null {
  const role = typeof message.role === 'string' ? message.role : message.type
  return typeof role === 'string' ? role.toLowerCase() : null
}

function contentText(content: unknown): string | null {
  if (typeof content === 'string') {
    const trimmed = content.trim()
    return trimmed || null
  }
  if (Array.isArray(content)) {
    const parts = content.map(contentBlockText).filter((part): part is string => Boolean(part))
    return parts.length ? parts.join('\n') : null
  }
  return null
}

function contentBlockText(block: unknown): string | null {
  if (typeof block === 'string') return block
  if (!isRecord(block)) return null
  const text = block.text
  return typeof text === 'string' ? text : null
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return value != null && typeof value === 'object' && !Array.isArray(value)
}

function parseJsonString(value: string): unknown {
  const trimmed = value.trim()
  if (!trimmed.startsWith('{') && !trimmed.startsWith('[')) return undefined
  try {
    return JSON.parse(trimmed)
  } catch {
    return undefined
  }
}

function numberValue(value: unknown): number | undefined {
  return typeof value === 'number' && Number.isFinite(value) ? value : undefined
}

function objectValue(value: unknown): Record<string, number> | undefined {
  return value && typeof value === 'object' && !Array.isArray(value)
    ? (value as Record<string, number>)
    : undefined
}

function countSpans(spans: TraceSpan[]): number {
  return spans.reduce((count, span) => count + 1 + countSpans(span.children ?? []), 0)
}
