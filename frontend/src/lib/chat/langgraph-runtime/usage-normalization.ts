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
  // 스트리밍 timing (TTFT/총시간/tok-s) — usage 옆에 실려 오므로 함께 복사.
  // 새 객체를 명시 빌드하는 화이트리스트라 추가하지 않으면 drop된다.
  const ttftMs = numberValue(payload.ttft_ms)
  if (ttftMs !== undefined) usage.ttft_ms = ttftMs
  const generationMs = numberValue(payload.generation_ms)
  if (generationMs !== undefined) usage.generation_ms = generationMs
  const tokensPerSecond = numberValue(payload.tokens_per_second)
  if (tokensPerSecond !== undefined) usage.tokens_per_second = tokensPerSecond
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
  const native = isRecord(usageMetadata) ? usageFromRecord(usageMetadata) : null

  // ``additional_kwargs.metadata.usage`` 는 우리 usage 이벤트 프로젝션이 써 넣은
  // enriched usage (token + cost + 스트리밍 timing). native ``usage_metadata`` 는
  // 토큰만 있고 cost/timing 이 없으므로, 토큰은 native 기준으로 두되 cost/timing 은
  // enriched 에서 보강한다(둘 다 같은 응답이라 토큰은 동일).
  const additionalKwargs = (message as { additional_kwargs?: unknown }).additional_kwargs
  const metadata =
    isRecord(additionalKwargs) && isRecord(additionalKwargs.metadata)
      ? additionalKwargs.metadata
      : null
  const enriched = isRecord(metadata?.usage) ? usageFromRecord(metadata.usage) : null

  if (native && enriched) {
    return {
      ...native,
      estimated_cost: native.estimated_cost ?? enriched.estimated_cost,
      ttft_ms: enriched.ttft_ms,
      generation_ms: enriched.generation_ms,
      tokens_per_second: enriched.tokens_per_second,
    }
  }
  return native ?? enriched
}
