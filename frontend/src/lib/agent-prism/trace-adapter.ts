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
  const rawRows = Array.isArray(detail?.raw) ? detail.raw : []
  if (rawRows.length > 0) {
    return rawRows.map((row, index) => normalizeRawObservation(row, trace, index))
  }
  const spans = detail?.spans?.length ? detail.spans : [rootSpanFromTrace(trace)]
  return spans.map((span, index) => spanToObservation(span, trace, index))
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

function normalizeRawObservation(
  row: Record<string, unknown>,
  trace: DebugTraceSummary,
  index: number,
): LangfuseObservation {
  const id = String(row.id ?? `observation:${index + 1}`)
  const startTime = stringValue(row.startTime ?? row.start_time) ?? trace.started_at
  const endTime =
    stringValue(row.endTime ?? row.end_time) ?? trace.completed_at ?? startTime ?? trace.started_at
  const rawType = stringValue(row.type) ?? 'SPAN'
  const level = levelFromStatus(stringValue(row.level ?? row.status))

  return {
    id,
    traceId: String(row.traceId ?? row.trace_id ?? trace.trace_id),
    projectId: String(row.projectId ?? row.project_id ?? DEFAULT_PROJECT_ID),
    environment: String(row.environment ?? DEFAULT_ENVIRONMENT),
    parentObservationId:
      stringValue(row.parentObservationId ?? row.parent_observation_id) || null,
    startTime,
    endTime,
    name: String(row.name ?? rawType.toLowerCase()),
    metadata: metadataForAgentPrism(row.metadata),
    type: typeFromValue(rawType),
    level,
    input: stringifyForAgentPrism(row.input),
    output: stringifyForAgentPrism(row.output),
    statusMessage: stringValue(row.statusMessage ?? row.status_message),
    version: stringValue(row.version),
    promptId: stringValue(row.promptId ?? row.prompt_id),
    createdAt: stringValue(row.createdAt ?? row.created_at) ?? startTime,
    updatedAt: stringValue(row.updatedAt ?? row.updated_at) ?? endTime,
    latency: numberValue(row.latency),
    timeToFirstToken: numberValue(row.timeToFirstToken ?? row.time_to_first_token),
    model: stringValue(row.model ?? row.providedModelName),
    internalModelId: stringValue(row.internalModelId ?? row.internal_model_id),
    promptName: stringValue(row.promptName ?? row.prompt_name),
    promptVersion: numberValue(row.promptVersion ?? row.prompt_version),
    usageDetails: objectValue(row.usageDetails ?? row.usage ?? row.usage_details),
    costDetails: objectValue(row.costDetails ?? row.cost ?? row.cost_details),
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
    input: stringifyForAgentPrism(span.input),
    output: stringifyForAgentPrism(span.output),
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
    input: { provider: trace.provider },
    output: trace.fallback_reason ? { fallback_reason: trace.fallback_reason } : null,
    metadata: {
      moldy_run_id: trace.moldy_run_id,
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

function typeFromValue(value: string): LangfuseObservationType {
  const normalized = value.toUpperCase()
  const allowed: LangfuseObservationType[] = [
    'EVENT',
    'SPAN',
    'GENERATION',
    'AGENT',
    'TOOL',
    'CHAIN',
    'RETRIEVER',
    'EVALUATOR',
    'EMBEDDING',
    'GUARDRAIL',
    'UNKNOWN',
  ]
  return allowed.includes(normalized as LangfuseObservationType)
    ? (normalized as LangfuseObservationType)
    : 'SPAN'
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

function stringifyForAgentPrism(value: unknown): string | null {
  if (value == null) return null
  if (typeof value === 'string') return value
  return JSON.stringify(value, null, 2)
}

function stringValue(value: unknown): string | null {
  return typeof value === 'string' && value.length > 0 ? value : null
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
