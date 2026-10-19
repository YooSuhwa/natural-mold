import type { ExcludedCapabilityGroup } from '@/lib/stores/chat-excluded-capabilities'

/** Backend `event_names.EXCLUDED_CAPABILITIES` (v3 custom name and legacy SSE event name). */
export const EXCLUDED_CAPABILITIES_EVENT = 'moldy.excluded_capabilities'

interface ProtocolCustomEvent {
  readonly method?: string
  readonly event_id?: string
  readonly seq?: number
  readonly params?: { readonly data?: unknown }
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
}

function text(value: unknown): string | undefined {
  return typeof value === 'string' && value.trim() ? value : undefined
}

/** Parse `{items: [{type, reason, count}]}`; `null` when nothing usable was excluded. */
export function parseExcludedCapabilities(value: unknown): ExcludedCapabilityGroup[] | null {
  if (!isRecord(value) || !Array.isArray(value.items)) return null
  const groups: ExcludedCapabilityGroup[] = []
  for (const item of value.items) {
    if (!isRecord(item)) continue
    const type = text(item.type)
    const reason = item.reason === 'broken' || item.reason === 'denied' ? item.reason : null
    const count = typeof item.count === 'number' && item.count > 0 ? Math.floor(item.count) : 0
    if (!type || !reason || count === 0) continue
    groups.push({ type, reason, count })
  }
  return groups.length > 0 ? groups : null
}

function customName(event: ProtocolCustomEvent): string | undefined {
  const method = text(event.method)
  if (method?.startsWith('custom:')) return method.slice('custom:'.length)
  if (method !== 'custom' || !isRecord(event.params?.data)) return undefined
  return text(event.params.data.name) ?? text(event.params.data.channel)
}

/** v3 protocol custom event → groups (or `null` for any other event). */
export function protocolExcludedCapabilities(
  event: ProtocolCustomEvent,
): ExcludedCapabilityGroup[] | null {
  const name = customName(event)
  if (name !== EXCLUDED_CAPABILITIES_EVENT && name !== 'excluded_capabilities') return null
  const data = event.params?.data
  return parseExcludedCapabilities(isRecord(data) && isRecord(data.payload) ? data.payload : data)
}

/** The run id prefix of a backend protocol event id (`<run_id>:<suffix>`). */
export function protocolEventRunId(event: ProtocolCustomEvent): string | undefined {
  const id = text(event.event_id)
  const separator = id?.indexOf(':') ?? -1
  return id && separator > 0 ? id.slice(0, separator) : undefined
}

/** Root `lifecycle` `running` event → its run id (the run start that clears the banner). */
export function protocolRunStart(event: ProtocolCustomEvent): string | undefined {
  if (event.method !== 'lifecycle') return undefined
  const data = event.params?.data
  const namespace = (event.params as { readonly namespace?: unknown } | undefined)?.namespace
  if (Array.isArray(namespace) && namespace.length > 0) return undefined
  if (!isRecord(data) || data.event !== 'running') return undefined
  return protocolEventRunId(event)
}
