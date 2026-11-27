import type { ThreadMessage } from '@assistant-ui/react'
import type { RunActivity } from '@/lib/chat/langgraph-runtime/activity-types'
import type { CompactionMarker } from '@/lib/chat/langgraph-runtime/compaction-events'
import { upsertArtifactList } from '@/lib/stores/chat-artifacts'
import type { ArtifactSummary, FileEventPayload, Message, TokenUsageBreakdown } from '@/lib/types'
import { fileActivityPointer, fileActivitySummary, usageActivityBreakdown } from './activity-values'
import type { AgUiActivitySnapshot, AgUiMessageLink } from './chat-history-types'

export interface MessageActivityPresentation {
  usage?: TokenUsageBreakdown
  fileEvents?: FileChange[]
  compaction?: CompactionMarker & { readonly activityId?: string }
  compactionMessageId?: string
}

type FileChange = FileEventPayload | Pick<FileEventPayload, 'id' | 'version_id'>

type ActivityIndex = ReadonlyMap<string, readonly AgUiActivitySnapshot[]>

function text(value: unknown): string | undefined {
  return typeof value === 'string' && value.length > 0 ? value : undefined
}

function record(value: unknown): value is Record<string, unknown> {
  return value !== null && typeof value === 'object' && !Array.isArray(value)
}

function stringList(value: unknown): string[] {
  return Array.isArray(value) ? value.filter((item): item is string => !!text(item)) : []
}

function ownerIds(activity: AgUiActivitySnapshot): string[] {
  const { activityType, content, parentMessageId } = activity
  switch (activityType) {
    case 'moldy.compaction':
      return [parentMessageId, text(content.public_message_id)].filter((id): id is string => !!id)
    case 'moldy.usage':
      return [
        text(content.assistant_msg_id),
        text(content.public_message_id),
        parentMessageId,
      ].filter((id): id is string => !!id)
    case 'moldy.file_event': {
      const links = stringList(content.linked_message_ids)
      return links.length
        ? links
        : [text(content.public_message_id), parentMessageId].filter((id): id is string => !!id)
    }
    default:
      return []
  }
}

function addToMap<T>(map: Map<string, T[]>, key: string, value: T) {
  const list = map.get(key)
  if (list) list.push(value)
  else map.set(key, [value])
}

/** Scope aliases to the authorized branch and run. The activity's own ID is an entity
 * ID; it need not equal any assistant message ID. */
export function indexMoldyMessageActivities(
  activities: readonly AgUiActivitySnapshot[],
  links: readonly AgUiMessageLink[],
): ActivityIndex {
  const indexed = new Map<string, AgUiActivitySnapshot[]>()
  const messageLinks = new Map<string, AgUiMessageLink[]>()
  for (const link of links) {
    addToMap(messageLinks, link.messageId, link)
    if (link.uiMessageId && link.uiMessageId !== link.messageId)
      addToMap(messageLinks, link.uiMessageId, link)
  }
  for (const activity of activities) {
    const ids = new Set<string>()
    for (const owner of ownerIds(activity)) {
      const candidates = messageLinks.get(owner)
      if (!candidates?.length) {
        ids.add('wire:' + owner)
        continue
      }
      const scoped = candidates.filter(
        (link) =>
          (!activity.physicalRunId ||
            !link.physicalRunId ||
            link.physicalRunId === activity.physicalRunId) &&
          (link.subagentRunId ?? null) === (activity.subagentRunId ?? null),
      )
      const rows = new Set(scoped.flatMap((link) => (link.uiMessageId ? [link.uiMessageId] : [])))
      if (!scoped.length || rows.size > 1) continue
      for (const row of rows) ids.add('db:' + row)
      if (candidates.length === scoped.length && scoped.every((link) => link.messageId === owner)) {
        ids.add('wire:' + owner)
      }
    }
    // The SDK folds the whole child run into its own distinctly identified nested
    // assistant message. A child update must never decorate its parent's bubble.
    if (activity.subagentRunId && ids.size) {
      addToMap(indexed, 'child:' + activity.subagentRunId, activity)
      continue
    }
    for (const id of ids) addToMap(indexed, id, activity)
  }
  return indexed
}

interface ActivityMessage {
  readonly id: string
  readonly metadata?: { readonly custom?: Record<string, unknown> }
}

function wireMessageId(message: ActivityMessage): string {
  const agui = message.metadata?.custom?.agui
  return (record(agui) ? text(agui.messageId) : undefined) ?? message.id
}

export function messageActivityPresentation(
  index: ActivityIndex,
  message: ActivityMessage,
  conversationId?: string,
): MessageActivityPresentation | undefined {
  const custom = message.metadata?.custom
  const keys = ['wire:' + wireMessageId(message)]
  if (typeof custom?.publicMessageId === 'string') keys.push('db:' + custom.publicMessageId)
  if (record(custom?.agui) && typeof custom.agui.name === 'string') {
    keys.push('child:' + message.id)
  }
  const activities = new Set(keys.flatMap((id) => index.get(id) ?? []))
  if (activities.size === 0) return
  const result: MessageActivityPresentation = {}
  for (const activity of activities) {
    const { content } = activity
    if (activity.activityType === 'moldy.file_event') {
      const file = fileActivitySummary(content, conversationId)
      // A bare pointer also represents a pending or denied current lookup. Discard
      // a retained descriptor for exactly that version until authorization succeeds.
      const change = file ?? fileActivityPointer(content)
      if (change) {
        result.fileEvents ??= []
        result.fileEvents.push(change)
      }
    } else if (activity.activityType === 'moldy.usage') {
      const usage = usageActivityBreakdown(content)
      if (usage) result.usage = usage
    } else if (activity.activityType === 'moldy.compaction' && content.state === 'done') {
      const historyId = text(content.history_id)
      const cutoff = content.cutoff_index
      result.compaction = {
        ...(historyId &&
          /^history_[a-f0-9]{24}$/.test(historyId) && {
            historyId,
            activityId: activity.messageId,
          }),
        ...(typeof cutoff === 'number' &&
          Number.isSafeInteger(cutoff) &&
          cutoff >= 0 && { cutoffIndex: cutoff }),
      }
      result.compactionMessageId = activity.parentMessageId ?? text(content.public_message_id)
    }
  }
  return result
}

export function mergeActivityFiles(
  existing: readonly ArtifactSummary[],
  changes: readonly FileChange[] | undefined,
): readonly ArtifactSummary[] {
  let result = existing
  for (const change of changes ?? []) {
    const current = result.find((item) => item.id === change.id)
    if (!('version_number' in change)) {
      if (current?.version_id === change.version_id)
        result = result.filter((item) => item.id !== change.id)
      continue
    }
    if (current && current.version_number > change.version_number) continue
    result = upsertArtifactList(result, change)
  }
  return result
}

export function applyMoldyActivitiesToHistory(
  converted: readonly ThreadMessage[],
  activities: readonly AgUiActivitySnapshot[],
  serverMessages: readonly Message[],
  links: readonly AgUiMessageLink[],
): ThreadMessage[] {
  const index = indexMoldyMessageActivities(activities, links)
  const rows = new Map(
    serverMessages.filter((row) => row.role === 'assistant').map((row) => [row.id, row]),
  )
  const wireRows = new Map<string, Message | null>()
  for (const link of links) {
    const row = link.uiMessageId ? rows.get(link.uiMessageId) : undefined
    if (!row || link.subagentRunId) continue
    const known = wireRows.get(link.messageId)
    wireRows.set(link.messageId, known === undefined || known === row ? row : null)
  }
  const conversationId = serverMessages[0]?.conversation_id
  return converted.map((message) => {
    if (message.role !== 'assistant') return message
    const publicId = text(message.metadata.custom.publicMessageId)
    const row = publicId ? rows.get(publicId) : wireRows.get(wireMessageId(message))
    const presentation = messageActivityPresentation(index, message, conversationId)
    if (!row?.usage && !row?.artifacts?.length && !presentation) return message
    const artifacts = mergeActivityFiles(row?.artifacts ?? [], presentation?.fileEvents)
    const usage = presentation?.usage ?? row?.usage
    return {
      ...message,
      metadata: {
        ...message.metadata,
        custom: {
          ...message.metadata.custom,
          ...(usage && { usage }),
          ...((row?.artifacts || presentation?.fileEvents) && { artifacts }),
          ...(presentation?.compaction && {
            compaction: presentation.compaction,
            compactionMessageId: presentation.compactionMessageId,
          }),
        },
      },
    }
  })
}

export function toMoldyRunActivities(
  activities: readonly AgUiActivitySnapshot[],
  runId: string,
): RunActivity[] {
  const result = new Map<string, RunActivity>()
  for (const activity of activities) {
    if (activity.physicalRunId !== runId) continue
    let kind: RunActivity['kind']
    let status: RunActivity['status'] = 'complete'
    switch (activity.activityType) {
      case 'moldy.file_event':
        kind = 'artifact'
        if (activity.content.op === 'failed') status = 'error'
        break
      case 'moldy.compaction':
        kind = 'compaction'
        if (activity.content.state !== 'running' && activity.content.state !== 'done') continue
        status = activity.content.state === 'running' ? 'running' : 'complete'
        break
      case 'moldy.memory_proposed':
      case 'moldy.agent_memo_proposed':
        kind = 'memory'
        status = 'requires_action'
        break
      case 'moldy.memory_recalled':
      case 'moldy.memory_saved':
      case 'moldy.memory_deleted':
      case 'moldy.memory_rejected':
        kind = 'memory'
        break
      default:
        continue
    }
    const namespace = activity.subagentRunId ? [activity.subagentRunId] : []
    const id = JSON.stringify([runId, ...namespace, activity.messageId])
    result.set(id, { id, runId, kind, status, namespace, title: kind })
  }
  return [...result.values()]
}
