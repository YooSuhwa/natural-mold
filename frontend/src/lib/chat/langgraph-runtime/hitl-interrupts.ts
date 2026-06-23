import { AIMessage, ToolMessage, type BaseMessage } from '@langchain/core/messages'
import type {
  ActionRequest,
  Decision,
  DecisionType,
  ReviewConfig,
  StandardInterruptPayload,
  ToolCallInfo,
} from '@/lib/types'
import { standardInterruptToToolCalls } from '@/lib/chat/standard-interrupt'

export interface LangGraphInterruptLike {
  readonly id?: string
  readonly value?: unknown
  readonly interruptId?: string
  readonly payload?: unknown
  readonly namespace?: readonly unknown[]
  readonly ns?: readonly unknown[]
}

export interface ApprovalResult {
  decision: 'approved' | 'modified' | 'rejected'
  modified_args?: Record<string, unknown>
  reason?: string
}

export interface ResolvedInterruptToolCall {
  readonly toolCall: ToolCallInfo
  readonly result: ApprovalResult
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
}

function textValue(value: unknown): string | undefined {
  return typeof value === 'string' && value.trim() ? value : undefined
}

function arrayValue(value: unknown): readonly unknown[] | undefined {
  return Array.isArray(value) ? value : undefined
}

function recordValue(value: unknown): Record<string, unknown> | undefined {
  return isRecord(value) ? value : undefined
}

function namespaceValue(value: unknown): string[] | undefined {
  const raw = arrayValue(value)
  if (!raw) return undefined
  return raw.filter((item): item is string => typeof item === 'string')
}

function isString(value: string | undefined): value is string {
  return typeof value === 'string'
}

function decisionType(value: unknown): DecisionType | null {
  switch (value) {
    case 'approve':
    case 'edit':
    case 'reject':
    case 'respond':
      return value
    default:
      return null
  }
}

function parseActionRequest(value: unknown): ActionRequest | null {
  if (!isRecord(value)) return null
  const name = textValue(value.name)
  if (!name) return null
  const args = recordValue(value.args) ?? {}
  const description = textValue(value.description)
  return description ? { name, args, description } : { name, args }
}

function parseReviewConfig(value: unknown): ReviewConfig | null {
  if (!isRecord(value)) return null
  const actionName = textValue(value.action_name) ?? textValue(value.actionName)
  if (!actionName) return null
  const rawAllowed = arrayValue(value.allowed_decisions) ?? arrayValue(value.allowedDecisions) ?? []
  const allowed = rawAllowed
    .map((item) => decisionType(item))
    .filter((item): item is DecisionType => item !== null)
  if (allowed.length === 0) return null
  return { action_name: actionName, allowed_decisions: allowed }
}

function actionRequests(value: Record<string, unknown>): ActionRequest[] | null {
  const raw = arrayValue(value.action_requests) ?? arrayValue(value.actionRequests)
  if (!raw) return null
  const parsed = raw
    .map((item) => parseActionRequest(item))
    .filter((item): item is ActionRequest => item !== null)
  return parsed.length > 0 ? parsed : null
}

function reviewConfigs(value: Record<string, unknown>): ReviewConfig[] | null {
  const raw = arrayValue(value.review_configs) ?? arrayValue(value.reviewConfigs)
  if (!raw) return null
  const parsed = raw
    .map((item) => parseReviewConfig(item))
    .filter((item): item is ReviewConfig => item !== null)
  return parsed.length > 0 ? parsed : null
}

function nativeAskUserPayload(
  interruptId: string,
  value: Record<string, unknown>,
  namespace: string[] | undefined,
): StandardInterruptPayload | null {
  if (value.type !== 'ask_user') return null
  const args = Object.fromEntries(Object.entries(value).filter(([key]) => key !== 'type'))
  const normalizedArgs =
    'mode' in args
      ? args
      : {
          question: textValue(args.question) ?? '',
          options: arrayValue(args.options) ?? [],
        }
  return withNamespace(
    {
      interrupt_id: interruptId,
      action_requests: [{ name: 'ask_user', args: normalizedArgs }],
      review_configs: [{ action_name: 'ask_user', allowed_decisions: ['respond'] }],
    },
    namespace,
  )
}

function interruptNamespace(
  interrupt: LangGraphInterruptLike,
  value: Record<string, unknown>,
): string[] | undefined {
  return (
    namespaceValue(interrupt.namespace) ??
    namespaceValue(interrupt.ns) ??
    namespaceValue(value.namespace) ??
    namespaceValue(value.ns)
  )
}

function withNamespace(
  payload: StandardInterruptPayload,
  namespace: string[] | undefined,
): StandardInterruptPayload {
  if (!namespace) return payload
  return { ...payload, namespace }
}

function upsertPayload(
  byId: Map<string, StandardInterruptPayload>,
  order: string[],
  payload: StandardInterruptPayload,
): void {
  if (!byId.has(payload.interrupt_id)) order.push(payload.interrupt_id)
  const existing = byId.get(payload.interrupt_id)
  if (existing?.namespace && !payload.namespace) return
  byId.set(payload.interrupt_id, payload)
}

function orderedPayloads(
  byId: ReadonlyMap<string, StandardInterruptPayload>,
  order: readonly string[],
): StandardInterruptPayload[] {
  return order.flatMap((interruptId) => {
    const payload = byId.get(interruptId)
    return payload ? [payload] : []
  })
}

export function standardPayloadFromInterrupt(
  interrupt: LangGraphInterruptLike,
  index = 0,
): StandardInterruptPayload | null {
  const value = recordValue(interrupt.value) ?? recordValue(interrupt.payload)
  if (!value) return null
  const interruptId =
    textValue(interrupt.id) ??
    textValue(interrupt.interruptId) ??
    textValue(value.interrupt_id) ??
    textValue(value.interruptId) ??
    `interrupt-${index + 1}`
  const namespace = interruptNamespace(interrupt, value)
  const nativeAskUser = nativeAskUserPayload(interruptId, value, namespace)
  if (nativeAskUser) return nativeAskUser
  const actions = actionRequests(value)
  const reviews = reviewConfigs(value)
  if (!actions || !reviews) return null
  return withNamespace(
    {
      interrupt_id: interruptId,
      action_requests: actions,
      review_configs: reviews,
    },
    namespace,
  )
}

export function standardPayloadsFromInterrupts(
  interrupts: readonly LangGraphInterruptLike[],
): StandardInterruptPayload[] {
  const byId = new Map<string, StandardInterruptPayload>()
  const order: string[] = []
  for (const [index, interrupt] of interrupts.entries()) {
    const payload = standardPayloadFromInterrupt(interrupt, index)
    if (payload) upsertPayload(byId, order, payload)
  }
  return orderedPayloads(byId, order)
}

function hasToolCallId(message: BaseMessage, ids: ReadonlySet<string>): boolean {
  if (!AIMessage.isInstance(message)) return false
  return (
    message.tool_calls?.some((toolCall) => isString(toolCall.id) && ids.has(toolCall.id)) ?? false
  )
}

function sameArgs(left: Record<string, unknown>, right: Record<string, unknown>): boolean {
  return JSON.stringify(left) === JSON.stringify(right)
}

const HITL_METADATA_KEYS = new Set([
  'approval_id',
  'allowed_decisions',
  'hitl_interrupt_id',
  'hitl_action_index',
  'hitl_total_actions',
])

type MutableToolCall = {
  id?: string
  name: string
  args: Record<string, unknown>
  type?: string
}

function stripHitLMetadata(args: Record<string, unknown>): Record<string, unknown> {
  return Object.fromEntries(Object.entries(args).filter(([key]) => !HITL_METADATA_KEYS.has(key)))
}

function equivalentToolArgs(
  left: Record<string, unknown>,
  right: Record<string, unknown>,
): boolean {
  return JSON.stringify(stripHitLMetadata(left)) === JSON.stringify(stripHitLMetadata(right))
}

function mutableToolCalls(message: BaseMessage): MutableToolCall[] | null {
  if (!AIMessage.isInstance(message)) return null
  const toolCalls = message.tool_calls
  return Array.isArray(toolCalls) ? (toolCalls as MutableToolCall[]) : null
}

function hydratedRequiresActionMessage(
  message: BaseMessage,
  toolCalls: readonly MutableToolCall[],
  index: number,
  synthetic: ToolCallInfo,
): BaseMessage {
  const existing = toolCalls[index]
  if (!existing) return message
  const nextToolCalls = [...toolCalls]
  nextToolCalls[index] = {
    ...existing,
    args: {
      ...existing.args,
      ...synthetic.args,
      approval_id: existing.id ?? synthetic.args.approval_id,
    },
  }
  return Object.assign(Object.create(Object.getPrototypeOf(message)) as BaseMessage, message, {
    tool_calls: nextToolCalls,
    status: {
      type: 'requires-action' as const,
      reason: 'tool-calls' as const,
    },
  })
}

function hydrateExistingAskUserToolCallMetadata(
  messages: BaseMessage[],
  payload: StandardInterruptPayload,
): boolean {
  let hydrated = false
  const syntheticToolCalls = standardInterruptToToolCalls(payload)

  for (const synthetic of syntheticToolCalls) {
    if (synthetic.name !== 'ask_user') continue

    for (const [messageIndex, message] of messages.entries()) {
      const toolCalls = mutableToolCalls(message)
      if (!toolCalls) continue

      const index = toolCalls.findIndex(
        (existing) =>
          existing.name === synthetic.name && equivalentToolArgs(existing.args, synthetic.args),
      )
      if (index < 0) continue

      messages[messageIndex] = hydratedRequiresActionMessage(message, toolCalls, index, synthetic)
      hydrated = true
      break
    }
  }

  return hydrated
}

function hasExistingAskUserToolCall(
  messages: readonly BaseMessage[],
  toolCall: ToolCallInfo,
): boolean {
  if (toolCall.name !== 'ask_user') return false
  return messages.some((message) => {
    const toolCalls = mutableToolCalls(message)
    return (
      toolCalls?.some(
        (existing) =>
          existing.name === toolCall.name && equivalentToolArgs(existing.args, toolCall.args),
      ) ?? false
    )
  })
}

function completedToolCallIds(messages: readonly BaseMessage[]): Set<string> {
  const ids = new Set<string>()
  for (const message of messages) {
    if (ToolMessage.isInstance(message) && isString(message.tool_call_id)) {
      ids.add(message.tool_call_id)
    }
  }
  return ids
}

function actionHasCompletedToolResult(
  action: ActionRequest,
  messages: readonly BaseMessage[],
  completedIds: ReadonlySet<string>,
): boolean {
  for (const message of messages) {
    if (!AIMessage.isInstance(message)) continue
    for (const toolCall of message.tool_calls ?? []) {
      if (!isString(toolCall.id) || !completedIds.has(toolCall.id)) continue
      if (toolCall.name === action.name && sameArgs(toolCall.args, action.args)) return true
    }
  }
  return false
}

export function interruptPayloadResolvedByMessages(
  payload: StandardInterruptPayload,
  messages: readonly BaseMessage[],
): boolean {
  const completedIds = completedToolCallIds(messages)
  if (completedIds.size === 0) return false
  return payload.action_requests.every((action) =>
    actionHasCompletedToolResult(action, messages, completedIds),
  )
}

function resolvedInterruptId(item: ResolvedInterruptToolCall): string {
  return String(item.toolCall.args.hitl_interrupt_id ?? '')
}

export function activeInterruptPayloads(
  payloads: readonly StandardInterruptPayload[],
  messages: readonly BaseMessage[],
  resolved: readonly ResolvedInterruptToolCall[],
): StandardInterruptPayload[] {
  const resolvedIds = new Set([
    ...payloads
      .filter((payload) => interruptPayloadResolvedByMessages(payload, messages))
      .map((payload) => payload.interrupt_id),
    ...resolved.map(resolvedInterruptId),
  ])
  return payloads.filter((payload) => !resolvedIds.has(payload.interrupt_id))
}

function resultFromDecision(decision: Decision): ApprovalResult {
  switch (decision.type) {
    case 'approve':
      return { decision: 'approved' }
    case 'edit':
      return decision.edited_action
        ? { decision: 'modified', modified_args: decision.edited_action.args }
        : { decision: 'modified' }
    case 'reject':
      return decision.message
        ? { decision: 'rejected', reason: decision.message }
        : { decision: 'rejected' }
    case 'respond':
      return { decision: 'approved' }
  }
}

export function resolvedInterruptToolCallsFromDecisions(
  payload: StandardInterruptPayload,
  decisions: readonly Decision[],
): ResolvedInterruptToolCall[] {
  const toolCalls = standardInterruptToToolCalls(payload)
  return decisions.flatMap((decision, index) => {
    const toolCall = toolCalls[index]
    if (!toolCall) return []
    return [{ toolCall, result: resultFromDecision(decision) }]
  })
}

function interruptedAssistantMessage(payload: StandardInterruptPayload): BaseMessage {
  const toolCalls = standardInterruptToToolCalls(payload)
  return Object.assign(
    new AIMessage({
      id: `moldy-hitl:${payload.interrupt_id}`,
      content: '',
      tool_calls: toolCalls.map((toolCall, index) => ({
        id: toolCall.id ?? `${payload.interrupt_id}:${index}`,
        name: toolCall.name,
        args: toolCall.args,
      })),
    }),
    {
      status: {
        type: 'requires-action' as const,
        reason: 'tool-calls' as const,
      },
    },
  )
}

export function appendInterruptToolCallMessages(
  messages: readonly BaseMessage[],
  payloads: readonly StandardInterruptPayload[],
): BaseMessage[] {
  if (payloads.length === 0) return [...messages]
  const projected: BaseMessage[] = [...messages]
  for (const payload of payloads) {
    const toolCalls = standardInterruptToToolCalls(payload)
    if (hydrateExistingAskUserToolCallMetadata(projected, payload)) continue
    const ids = new Set(toolCalls.map((toolCall) => toolCall.id).filter(isString))
    if (ids.size === 0 || projected.some((message) => hasToolCallId(message, ids))) continue
    projected.push(interruptedAssistantMessage(payload))
  }
  return projected
}

export function appendResolvedInterruptToolCallMessages(
  messages: readonly BaseMessage[],
  resolved: readonly ResolvedInterruptToolCall[],
): BaseMessage[] {
  if (resolved.length === 0) return [...messages]
  const projected: BaseMessage[] = [...messages]
  for (const item of resolved) {
    const toolCallId = item.toolCall.id
    if (!toolCallId) continue
    if (hasExistingAskUserToolCall(projected, item.toolCall)) continue
    if (!projected.some((message) => hasToolCallId(message, new Set([toolCallId])))) {
      projected.push(
        Object.assign(
          new AIMessage({
            id: `moldy-hitl-resolved:${toolCallId}`,
            content: '',
            tool_calls: [
              {
                id: toolCallId,
                name: item.toolCall.name,
                args: item.toolCall.args,
              },
            ],
          }),
          { status: { type: 'complete' as const } },
        ),
      )
    }
    projected.push(
      new ToolMessage({
        id: `moldy-hitl-result:${toolCallId}`,
        content: JSON.stringify(item.result),
        name: item.toolCall.name,
        tool_call_id: toolCallId,
      }),
    )
  }
  return projected
}
