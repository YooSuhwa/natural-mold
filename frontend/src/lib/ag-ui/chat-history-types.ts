import type { Interrupt, Message } from '@ag-ui/core'
import { ActivitySnapshotEventSchema, InterruptSchema, MessageSchema } from '@ag-ui/core/schemas'
import { z } from 'zod'
import {
  parsePartialModelOutput,
  type PartialModelOutput,
} from '@/lib/chat/langgraph-runtime/partial-model-output'
import { recoveryErrorCode } from '@/lib/chat-runs/recovery-error'
import type { AgUiHistoryChild } from './chat-history-children'

const id = z.string().min(1)
const nullableId = id.nullable().default(null)
const optionalId = id
  .nullish()
  .transform((value) => value ?? undefined)
  .optional()
const object = z.record(z.string(), z.unknown())

const messageLink = z.object({
  messageId: id,
  uiMessageId: nullableId,
  runId: nullableId,
  physicalRunId: nullableId,
  checkpointId: nullableId,
  parentCheckpointId: nullableId,
  parentMessageId: nullableId,
  subagentRunId: optionalId,
  parentToolCallId: optionalId,
  siblingCheckpointIds: z.array(id).optional(),
  createdAt: optionalId,
})
export type AgUiMessageLink = z.output<typeof messageLink>

const state = z.object({
  todos: z.array(
    z.object({
      id,
      content: z.string(),
      status: z.enum(['pending', 'in_progress', 'completed']),
    }),
  ),
})

const child = z.object({
  subagentRunId: id,
  parentToolCallId: optionalId,
  parentRunId: optionalId,
  runId: optionalId,
  physicalRunId: optionalId,
  parentSubagentRunId: optionalId,
  parentMessageId: optionalId,
  childTaskId: optionalId,
  name: id,
  status: z.enum(['running', 'completed', 'failed', 'cancelled', 'interrupted']),
  description: optionalId,
  result: z
    .string()
    .nullish()
    .transform((value) => value ?? undefined),
  error: optionalId,
  interruptIds: z.array(id).optional(),
  todos: state.shape.todos.optional(),
})

const envelope = z.object({
  threadId: id,
  protocolVersion: z.literal('1.0'),
  projectionVersion: z.literal('1'),
  checkpointId: nullableId,
  activeTipMessageId: nullableId,
  activeRunId: nullableId,
  physicalRunId: nullableId,
  interruptRunId: nullableId,
  physicalInterruptRunId: nullableId,
  failedPhysicalRunId: nullableId,
  latestPhysicalRunId: nullableId,
  errorCode: nullableId,
  runId: nullableId,
  runStatus: nullableId,
  queueStatus: nullableId,
  position: z.number().int().nonnegative().nullable().default(null),
  cursor: nullableId,
  todos: state.shape.todos.default([]),
  partialOutput: z.unknown().optional(),
  messages: z.array(z.unknown()),
  messageLinks: z.array(messageLink),
  subagents: z.array(z.unknown()),
  interrupts: z.array(z.unknown()),
})

export interface AgUiActivitySnapshot {
  readonly type: 'ACTIVITY_SNAPSHOT'
  readonly messageId: string
  readonly activityType: string
  readonly content: Record<string, unknown>
  readonly subagentRunId?: string
  readonly parentMessageId?: string
  readonly parentToolCallId?: string
  readonly physicalRunId?: string
}

export type WireChatSnapshot = Omit<
  z.output<typeof envelope>,
  'messages' | 'activities' | 'interrupts' | 'subagents' | 'partialOutput'
> & {
  messages: Message[]
  activities: AgUiActivitySnapshot[]
  state: z.output<typeof state>
  interrupts: Interrupt[]
  subagents: AgUiHistoryChild[]
  partialOutput?: PartialModelOutput | null
}

const publicActivityFields: Readonly<Record<string, readonly string[]>> = {
  'moldy.compaction': ['state', 'history_id', 'cutoff_index'],
  'moldy.usage': [
    'run_id',
    'assistant_msg_id',
    'prompt_tokens',
    'completion_tokens',
    'cache_creation_tokens',
    'cache_read_tokens',
    'estimated_cost',
    'ttft_ms',
    'generation_ms',
    'tokens_per_second',
  ],
  'moldy.file_event': ['id', 'version_id', 'op'],
  'moldy.agent_memo_proposed': ['scope', 'id'],
  'moldy.subagent_todos': ['todos'],
  'moldy.run_steer': ['conversation_id', 'run_id', 'steer_id', 'status'],
  'moldy.mcp_app': ['run_id', 'binding_id', 'tool_call_id', 'public_tool_call_id'],
  'moldy.ui_data': ['schema_version', 'type', 'props', 'message_id', 'run_id', 'tool_call_id'],
  'moldy.excluded_capabilities': ['items', 'total'],
  'moldy.subagent_names': ['names'],
  'moldy.memory_recalled': ['memories'],
  'moldy.skill_draft': [
    'session_id',
    'mode',
    'slug',
    'file_count',
    'files',
    'changed_count',
    'credential_requirement_count',
  ],
  'moldy.skill_validation': ['tool_name', 'session_id', 'validation_result'],
}
const memoryKinds = new Set([
  'moldy.memory_proposed',
  'moldy.memory_saved',
  'moldy.memory_rejected',
  'moldy.memory_deleted',
])
const memoryFields = ['scope', 'id', 'line_id', 'proposal_id', 'memory_id', 'path']

function pick(record: Record<string, unknown>, fields: readonly string[]): Record<string, unknown> {
  return Object.fromEntries(
    fields.filter((key) => Object.hasOwn(record, key)).map((key) => [key, record[key]]),
  )
}

function memoryPointer(value: Record<string, unknown>) {
  return { ...pick(value, memoryFields), content: '<redacted>' }
}

export function parsePublicActivities(raw: unknown): AgUiActivitySnapshot[] {
  if (!Array.isArray(raw)) return []
  const activities: AgUiActivitySnapshot[] = []
  for (const item of raw) {
    const result = ActivitySnapshotEventSchema.safeParse(item)
    if (!result.success || result.data.rawEvent !== undefined || !result.data.messageId) continue
    const event = result.data
    let content: Record<string, unknown>
    if (memoryKinds.has(event.activityType)) {
      content = memoryPointer(event.content)
    } else {
      if (!Object.hasOwn(publicActivityFields, event.activityType)) continue
      content = pick(event.content, publicActivityFields[event.activityType])
      if (event.activityType === 'moldy.memory_recalled') {
        const rows = z.array(object).safeParse(content.memories)
        if (!rows.success) continue
        content.memories = rows.data.map(memoryPointer)
      } else if (event.activityType === 'moldy.agent_memo_proposed') {
        if (content.scope !== 'agent_memo' || typeof content.id !== 'string') continue
        content.content = '<redacted>'
      } else if (event.activityType === 'moldy.subagent_todos') {
        const tasks = state.safeParse(content)
        if (!event.subagentRunId || !tasks.success) continue
        content = tasks.data
      }
    }
    if (typeof event.content.public_message_id === 'string') {
      content.public_message_id = event.content.public_message_id
    }
    activities.push({
      type: 'ACTIVITY_SNAPSHOT',
      messageId: event.messageId,
      activityType: event.activityType,
      content,
      subagentRunId: event.subagentRunId,
      parentMessageId:
        typeof event.parentMessageId === 'string' ? event.parentMessageId : undefined,
      parentToolCallId:
        typeof event.parentToolCallId === 'string' ? event.parentToolCallId : undefined,
      physicalRunId: typeof event.physicalRunId === 'string' ? event.physicalRunId : undefined,
    })
  }
  return activities
}

const askOption = z.union([
  z.string(),
  z.object({
    id: id.optional(),
    label: z.string(),
    description: z.string().optional(),
    disabled: z.boolean().optional(),
  }),
])
const ask = z.object({
  type: z.literal('ask_user'),
  question: z.string().optional(),
  options: z.array(askOption).optional(),
  mode: z.enum(['option_list', 'question_flow']).optional(),
  title: z.string().optional(),
  minSelections: z.number().int().optional(),
  maxSelections: z.number().int().optional(),
  questions: z
    .array(
      z.object({
        id: id.optional(),
        label: z.string().optional(),
        question: z.string().optional(),
        type: z.enum(['single_select', 'multi_select', 'text']).optional(),
        options: z.array(askOption).optional(),
        required: z.boolean().optional(),
      }),
    )
    .optional(),
})
const approvals = z.object({
  action_requests: z.array(
    z.object({ name: id, args: object, description: z.string().optional() }),
  ),
  review_configs: z.array(
    z.object({
      action_name: id,
      allowed_decisions: z.array(z.enum(['approve', 'reject', 'edit', 'respond'])),
      session_consent_eligible: z.boolean().optional(),
    }),
  ),
})
const auth = z.object({
  type: z.literal('auth_required'),
  auth_request_id: id.optional(),
  reason: z.string(),
  attempt: z.number().int().positive(),
  items: z.array(
    z
      .object({
        resource_name: id,
        resource_type: z.string().optional(),
        tool_names: z.array(id).optional(),
        fixable_by: z.enum(['me', 'owner']),
        credential_id: id.optional(),
        connect: z.object({ kind: id }).optional(),
      })
      .transform(({ credential_id, ...item }) =>
        item.fixable_by === 'me' && credential_id ? { ...item, credential_id } : item,
      ),
  ),
})
const interruptDetails = new Map<string, z.ZodType<Record<string, unknown>>>([
  ['moldy.ask_user', ask],
  ['moldy.auth_required', auth],
  ['moldy.tool_approval', approvals],
])

export function parsePublicInterrupts(raw: unknown): Interrupt[] {
  if (!Array.isArray(raw)) throw new Error('Invalid AG-UI interrupts')
  const active = new Map<string, { interrupt: Interrupt; attempt: number }>()
  const owners = new Map<string, string>()
  for (const value of raw) {
    const parsed = InterruptSchema.safeParse(value)
    if (!parsed.success || !parsed.data.id) throw new Error('Invalid AG-UI interrupt')
    const { id, reason, message, toolCallId, subagentRunId, expiresAt, responseSchema, metadata } =
      parsed.data
    const details = interruptDetails.get(reason)?.safeParse(metadata?.moldy)
    const owner = subagentRunId ?? ''
    if (owners.has(id) && owners.get(id) !== owner)
      throw new Error('Ambiguous AG-UI interrupt owner')
    owners.set(id, owner)
    const interrupt: Interrupt = {
      id,
      reason,
      message,
      toolCallId,
      subagentRunId,
      expiresAt,
      responseSchema,
      ...(details?.success && { metadata: { moldy: details.data } }),
    }
    const native = details?.success ? details.data : null
    const requestId =
      reason === 'moldy.auth_required' &&
      typeof native?.auth_request_id === 'string' &&
      native.auth_request_id !== '<redacted>'
        ? native.auth_request_id
        : id
    const key = JSON.stringify([owner, reason, requestId])
    const attempt = typeof native?.attempt === 'number' ? native.attempt : 0
    const previous = active.get(key)
    if (previous?.attempt === attempt && previous.interrupt.id !== id) {
      throw new Error('Ambiguous AG-UI interrupt attempt')
    }
    if (!previous || previous.attempt <= attempt) active.set(key, { interrupt, attempt })
  }
  return Array.from(active.values(), (entry) => entry.interrupt)
}

/** Attachments resolve through the authorized files query. Persisted chat only carries visible text. */
function publicTranscript(
  rows: readonly unknown[],
  links: readonly AgUiMessageLink[],
  children: readonly AgUiHistoryChild[],
) {
  const messages: Message[] = []
  const events: Record<string, unknown>[] = []
  const linksById = new Map<string, AgUiMessageLink | null>()
  for (const link of links) {
    linksById.set(link.messageId, linksById.has(link.messageId) ? null : link)
  }
  const childIds = new Set(children.map((child) => child.subagentRunId))
  for (const row of rows) {
    const parsed = MessageSchema.safeParse(row)
    if (!parsed.success || !parsed.data.id) throw new Error('Invalid AG-UI message')
    const message = parsed.data
    const link = linksById.get(message.id)
    if (!link || (message.subagentRunId ?? null) !== (link.subagentRunId ?? null)) continue
    if (link.subagentRunId && !childIds.has(link.subagentRunId)) continue
    const identity = { id: message.id, subagentRunId: message.subagentRunId }
    if (message.role === 'activity') {
      events.push({
        type: 'ACTIVITY_SNAPSHOT',
        messageId: message.id,
        activityType: message.activityType,
        content: message.content,
        subagentRunId: message.subagentRunId,
        rawEvent: message.rawEvent,
        parentMessageId: link.parentMessageId,
        parentToolCallId: link.parentToolCallId,
        physicalRunId: link.physicalRunId,
      })
    } else if (message.role === 'assistant') {
      const toolCalls = message.toolCalls?.map(({ id, type, function: call }) => ({
        id,
        type,
        function: { name: call.name, arguments: call.arguments },
      }))
      messages.push({ ...identity, role: 'assistant', content: message.content, toolCalls })
    } else if (message.role === 'user' || message.role === 'tool') {
      const content =
        typeof message.content === 'string'
          ? message.content
          : message.content.flatMap((part) =>
              part.type === 'text' ? [{ type: 'text' as const, text: part.text }] : [],
            )
      messages.push(
        message.role === 'user'
          ? { ...identity, role: 'user', content }
          : { ...identity, role: 'tool', content, toolCallId: message.toolCallId },
      )
    }
  }
  return { messages, activities: parsePublicActivities(events) }
}

const standardErrors = new Set([
  'run_failed',
  'runtime_error',
  'stream_error',
  'worker_lost',
  'worker_shutdown',
  'prepared_run_start_failed',
  'pending_input_unavailable',
  'partial_model_stream',
  'tenant_suspended',
  'forbidden',
  'organization_archived',
  'prepared_run_lease_missing',
  'auth_required',
])

/** Official AG-UI schemas accept extension fields. Only the Moldy public contract enters the SDK. */
export function parseWireChatSnapshot(raw: unknown, expectedThreadId: string): WireChatSnapshot {
  const parsed = envelope.safeParse(raw)
  if (!parsed.success || parsed.data.threadId !== expectedThreadId) {
    throw new Error('Invalid AG-UI chat snapshot')
  }
  const { messages, interrupts, subagents, partialOutput, ...snapshot } = parsed.data
  if (Boolean(snapshot.activeRunId) !== Boolean(snapshot.physicalRunId)) {
    throw new Error('Invalid AG-UI active run identity')
  }
  const children = subagents.flatMap((row) => {
    const parsed = child.safeParse(row)
    return parsed.success ? [parsed.data] : []
  })
  return {
    ...snapshot,
    errorCode: standardErrors.has(snapshot.errorCode ?? '')
      ? snapshot.errorCode
      : (recoveryErrorCode(snapshot.errorCode) ?? null),
    state: { todos: snapshot.todos },
    subagents: children,
    ...publicTranscript(messages, snapshot.messageLinks, children),
    interrupts: parsePublicInterrupts(interrupts),
    ...(snapshot.failedPhysicalRunId &&
      snapshot.errorCode === 'partial_model_stream' && {
        partialOutput: parsePartialModelOutput(partialOutput),
      }),
  }
}
