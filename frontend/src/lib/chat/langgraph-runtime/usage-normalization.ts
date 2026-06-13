import type { BaseMessage } from '@langchain/core/messages'
import type { TokenUsageBreakdown } from '@/lib/types'

export interface ProtocolUsageEvent {
  readonly method?: string
  readonly event_id?: string
  readonly seq?: number
  readonly run_id?: string
  readonly params?: {
    readonly data?: unknown
  }
}

export type UsagePayload = TokenUsageBreakdown & {
  assistant_msg_id?: string
  run_id?: string
}

export function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
}

export function textValue(value: unknown): string | undefined {
  return typeof value === 'string' && value.trim() ? value : undefined
}

function numberValue(value: unknown): number | undefined {
  return typeof value === 'number' && Number.isFinite(value) ? value : undefined
}

function customName(event: Record<string, unknown>): string | undefined {
  const method = textValue(event.method)
  if (method?.startsWith('custom:')) return method.slice(7)
  const params = isRecord(event.params) ? event.params : null
  const data = isRecord(params?.data) ? params.data : null
  if (method !== 'custom' || !data) return undefined
  return textValue(data.name) ?? textValue(data.channel)
}

function normalizeCustomName(name: string | undefined): string | undefined {
  if (!name) return undefined
  return name.startsWith('moldy.') ? name.slice('moldy.'.length) : name
}

function payloadCandidate(data: unknown): unknown {
  if (isRecord(data) && isRecord(data.payload)) return data.payload
  return data
}

function rawPayloadCandidate(event: Record<string, unknown>): unknown {
  const params = isRecord(event.params) ? event.params : null
  return params && 'data' in params ? payloadCandidate(params.data) : payloadCandidate(event)
}

function usageFromRecord(payload: Record<string, unknown>): TokenUsageBreakdown | null {
  const promptTokens = numberValue(payload.prompt_tokens ?? payload.input_tokens)
  const completionTokens = numberValue(payload.completion_tokens ?? payload.output_tokens)
  const inputDetails = isRecord(payload.input_token_details) ? payload.input_token_details : {}
  const cacheCreationTokens =
    numberValue(payload.cache_creation_tokens ?? inputDetails.cache_creation) ?? 0
  const cacheReadTokens = numberValue(payload.cache_read_tokens ?? inputDetails.cache_read) ?? 0
  if (promptTokens === undefined || completionTokens === undefined) {
    return null
  }

  const usage: TokenUsageBreakdown = {
    prompt_tokens: promptTokens,
    completion_tokens: completionTokens,
    cache_creation_tokens: cacheCreationTokens,
    cache_read_tokens: cacheReadTokens,
  }
  const estimatedCost = numberValue(payload.estimated_cost)
  if (estimatedCost !== undefined) {
    usage.estimated_cost = estimatedCost
  }
  return usage
}

export function protocolUsagePayload(event: unknown): UsagePayload | null {
  if (!isRecord(event)) return null

  const method = textValue(event.method)
  const eventName = normalizeCustomName(customName(event))
  const isDedicatedPayload = method === undefined
  if (!isDedicatedPayload && eventName !== 'usage') return null

  const params = isRecord(event.params) ? event.params : null
  const payload = isDedicatedPayload ? rawPayloadCandidate(event) : payloadCandidate(params?.data)
  if (!isRecord(payload)) return null

  const usage = usageFromRecord(payload)
  if (!usage) return null

  const result: UsagePayload = { ...usage }
  const assistantMsgId = textValue(payload.assistant_msg_id)
  if (assistantMsgId) {
    result.assistant_msg_id = assistantMsgId
  }
  const runId = textValue(payload.run_id)
  if (runId) {
    result.run_id = runId
  }
  return result
}

export function usageFromMessage(message: BaseMessage): TokenUsageBreakdown | null {
  const usageMetadata = (message as { usage_metadata?: unknown }).usage_metadata
  if (isRecord(usageMetadata)) {
    return usageFromRecord(usageMetadata)
  }
  const additionalKwargs = (message as { additional_kwargs?: unknown }).additional_kwargs
  const metadata =
    isRecord(additionalKwargs) && isRecord(additionalKwargs.metadata)
      ? additionalKwargs.metadata
      : null
  const usage = metadata?.usage
  return isRecord(usage) ? usageFromRecord(usage) : null
}
