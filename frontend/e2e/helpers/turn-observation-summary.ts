export function turnRecord(value: unknown): Record<string, unknown> {
  return value !== null && typeof value === 'object' && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : {}
}

function rows(value: unknown, limit: number) {
  return Array.isArray(value) ? value.slice(0, limit).map(turnRecord) : []
}

export function turnKind(value: unknown, allowed: readonly string[]) {
  return typeof value === 'string' && allowed.includes(value) ? value : 'other'
}

function slots() {
  const ids = new Map<string, number>()
  return (value: unknown) => {
    if (typeof value !== 'string' || !value || value.length > 256) return 0
    if (!ids.has(value) && ids.size < 64) ids.set(value, ids.size + 1)
    return ids.get(value) ?? 0
  }
}

export interface ObservedTurnCommand {
  thread: string
  wire: 'ag_ui' | 'v3'
  strategy: string
  id?: string
  status: number
  receiptInput?: string
  receiptRun?: string
}

export function summarizeTurns(
  conversationId: string,
  requests: ObservedTurnCommand[],
  messages: Record<string, unknown>,
  history: Record<string, unknown>,
  inputs: Record<string, unknown>,
) {
  const requestSlot = slots()
  const physicalSlot = slots()
  const inputSlot = slots()
  const messageSlot = slots()
  const inputRows = rows(inputs.items, 8).filter((item) => item.conversation_id === conversationId)
  const commands = requests
    .filter((entry) => entry.thread === conversationId)
    .map(({ wire, strategy, id, receiptInput, receiptRun, status }) => {
      const receipt =
        wire === 'v3' && typeof id === 'string'
          ? inputRows.find((item) => item.client_request_id === id)
          : undefined
      return {
        wire,
        strategy,
        status,
        requestSlot: requestSlot(id),
        physicalSlot: physicalSlot(receiptRun ?? receipt?.run_id),
        inputSlot: inputSlot(receiptInput ?? receipt?.id),
      }
    })
  const stored = rows(messages.messages, 16).map((msg) => ({
    role: turnKind(msg.role, ['user', 'assistant']),
    slot: messageSlot(msg.id),
    hasContent: typeof msg.content === 'string' && msg.content.length > 0,
  }))
  const runs = rows(history.runs, 8)
    .filter((row) => row.conversation_id === conversationId)
    .map((row) => ({
      physicalSlot: physicalSlot(row.id),
      source: turnKind(row.source, ['chat', 'start', 'edit', 'regenerate', 'resume']),
      status: turnKind(row.status, [
        'queued',
        'running',
        'canceling',
        'completed',
        'interrupted',
        'failed',
        'canceled',
        'stale',
      ]),
    }))
  const items = inputRows.map((row) => ({
    slot: inputSlot(row.id),
    physicalSlot: physicalSlot(row.run_id),
    status: turnKind(row.status, ['pending', 'claimed', 'failed', 'canceled']),
  }))
  return {
    commands,
    stored,
    runs,
    items,
    queuePaused: typeof inputs.queue_paused === 'boolean' ? inputs.queue_paused : null,
  }
}
