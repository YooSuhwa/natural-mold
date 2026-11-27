import type { Message } from '@ag-ui/core'
import { fromAgUiMessages } from '@assistant-ui/react-ag-ui'
import {
  fromThreadMessageLike,
  type ExportedMessageRepository,
  type ThreadMessage,
} from '@assistant-ui/react'
import { resolveDataUI } from '@/lib/chat/data-ui-registry'
import type { Message as MoldyMessage } from '@/lib/types'
import { parseTimestamp } from '@/lib/utils/format-relative-time'
import { applyMoldyActivitiesToHistory } from './activity-projection'
import { nestAgUiHistoryChildren } from './chat-history-children'
import { restoreAgUiInterrupts } from './chat-history-interrupts'
import type { AgUiActivitySnapshot, AgUiMessageLink, WireChatSnapshot } from './chat-history-types'

interface SavedRecord {
  readonly message: Message
  readonly link: AgUiMessageLink
  readonly childId: string | undefined
}

function addToGroup<K, V>(map: Map<K, V[]>, key: K, value: V) {
  const group = map.get(key)
  if (group) group.push(value)
  else map.set(key, [value])
}

function ownedRecords(snapshot: WireChatSnapshot): SavedRecord[] {
  const links = new Map<string, AgUiMessageLink[]>()
  for (const link of snapshot.messageLinks) addToGroup(links, link.messageId, link)
  const children = new Set(snapshot.subagents.map((child) => child.subagentRunId))
  const records: SavedRecord[] = []
  const seen = new Set<AgUiMessageLink>()
  const repeated = new Set<AgUiMessageLink>()
  for (const message of snapshot.messages) {
    const candidates = links.get(message.id) ?? []
    const matching =
      message.subagentRunId || candidates.length > 1
        ? candidates.filter((link) => link.subagentRunId === message.subagentRunId)
        : candidates
    if (matching.length !== 1) continue
    const link = matching[0]
    const childId = link.subagentRunId
    if (childId && !children.has(childId)) continue
    if (seen.has(link)) repeated.add(link)
    seen.add(link)
    records.push({ message, link, childId })
  }
  return records.filter(({ link }) => !repeated.has(link))
}

/** Only fully linked root paths may choose a branch or act as a tool result's owner. */
function rootedIds(records: ReadonlyMap<string, SavedRecord>): Set<string> {
  const valid = new Set<string>()
  const invalid = new Set<string>()
  for (const id of records.keys()) {
    const path = new Set<string>()
    let next: string | null = id
    while (next !== null && !valid.has(next) && !invalid.has(next) && !path.has(next)) {
      path.add(next)
      const record = records.get(next)
      if (!record) break
      next = record.link.parentMessageId
    }
    const target = next === null || valid.has(next) ? valid : invalid
    for (const visited of path) target.add(visited)
  }
  return valid
}

function isReplayed({ message, link }: SavedRecord, snapshot: WireChatSnapshot): boolean {
  return (
    message.role !== 'user' &&
    snapshot.activeRunId !== null &&
    (link.runId === snapshot.activeRunId ||
      (snapshot.physicalRunId !== null && link.physicalRunId === snapshot.physicalRunId))
  )
}

function scopedExtras(
  records: readonly SavedRecord[],
  activities: readonly AgUiActivitySnapshot[],
) {
  const byId = new Map(records.map((record) => [record.message.id, record]))
  const aliases = new Map<string, SavedRecord[]>()
  const calls = new Map<string, SavedRecord[]>()
  const extras = new Map<string, Message[]>()
  for (const record of records) {
    if (record.message.role !== 'assistant') continue
    addToGroup(aliases, record.message.id, record)
    for (const call of record.message.toolCalls ?? []) addToGroup(calls, call.id, record)
    if (record.link.uiMessageId && record.link.uiMessageId !== record.message.id)
      addToGroup(aliases, record.link.uiMessageId, record)
  }
  for (const { message, link } of records) {
    if (message.role !== 'tool') continue
    // The same call ID can reappear on a sibling branch. The SDK joins the last
    // matching call in its input, so feed it only results from this ancestor.
    let parent = link.parentMessageId
    const visited = new Set<string>()
    while (parent && !visited.has(parent)) {
      visited.add(parent)
      const ancestor = byId.get(parent)
      if (!ancestor || ancestor.message.role === 'user') break
      if (
        ancestor.message.role === 'assistant' &&
        ancestor.message.toolCalls?.some((call) => call.id === message.toolCallId)
      ) {
        addToGroup(extras, parent, message)
        break
      }
      parent = ancestor.link.parentMessageId
    }
  }
  for (const activity of activities) {
    if (typeof activity.content.type !== 'string') continue
    const content = activity.content
    const ownerId =
      activity.parentMessageId ??
      (typeof content.message_id === 'string' ? content.message_id : undefined) ??
      (typeof content.public_message_id === 'string' ? content.public_message_id : undefined)
    const physicalRun =
      activity.physicalRunId ?? (typeof content.run_id === 'string' ? content.run_id : undefined)
    // A tool-only response can emit a card before any text starts. The explicit
    // call then identifies its owner only within the same physical run and lane.
    const callId = content.tool_call_id
    const candidates = ownerId
      ? aliases.get(ownerId)
      : physicalRun && typeof callId === 'string'
        ? calls.get(callId)
        : undefined
    const owners = (candidates ?? []).filter(
      ({ link, childId }) =>
        childId === activity.subagentRunId && (!physicalRun || link.physicalRunId === physicalRun),
    )
    if (owners.length !== 1) continue
    const resolved = resolveDataUI(activity.content.type, content.props)
    if (!resolved) continue
    addToGroup(extras, owners[0].message.id, {
      id: activity.messageId,
      role: 'activity',
      activityType: activity.activityType,
      subagentRunId: activity.subagentRunId,
      content: { type: activity.content.type, props: resolved.props },
    })
  }
  return extras
}

function restoredRoot(
  record: SavedRecord,
  extra: readonly Message[],
  row?: MoldyMessage,
): ThreadMessage {
  const [like] = fromAgUiMessages([record.message, ...extra], {
    showThinking: false,
    joinAdjacentAssistantMessages: false,
  })
  const { link } = record
  const created = link.createdAt ?? row?.created_at
  // Older journals have no timestamp. Never replace it with the time the tab reopened.
  const timestamp = created ? parseTimestamp(created).getTime() : 0
  const checkpoints = link.siblingCheckpointIds?.length
    ? link.siblingCheckpointIds
    : row?.sibling_checkpoint_ids
  const checkpointId = link.checkpointId ?? row?.branch_checkpoint_id
  const branchIndex = checkpointId && checkpoints ? checkpoints.indexOf(checkpointId) : -1
  const custom = {
    ...like.metadata?.custom,
    ...(link.uiMessageId && { publicMessageId: link.uiMessageId }),
    ...(checkpointId && { branchCheckpointId: checkpointId }),
    ...(link.parentCheckpointId && { parentCheckpointId: link.parentCheckpointId }),
    ...(checkpoints &&
      checkpoints.length > 1 &&
      branchIndex >= 0 && {
        siblingCheckpointIds: checkpoints,
        branchIndex,
        branchTotal: checkpoints.length,
        activeBranchId: link.uiMessageId,
      }),
  }
  const rating = like.role === 'assistant' ? row?.feedback?.rating : undefined
  return fromThreadMessageLike(
    {
      ...like,
      // Durable tool execution belongs to the worker. Only the checkpoint's
      // current interrupts below can request input from a reopening client.
      ...(like.status?.type === 'requires-action' &&
        like.status.reason === 'tool-calls' && {
          status: { type: 'complete', reason: 'unknown' } as const,
        }),
      id: record.message.id,
      createdAt: new Date(Number.isFinite(timestamp) ? timestamp : 0),
      metadata: {
        ...like.metadata,
        custom,
        ...(rating && { submittedFeedback: { type: rating === 'up' ? 'positive' : 'negative' } }),
      },
    },
    record.message.id,
    { type: 'complete', reason: 'stop' },
  )
}

export function buildAgUiChatRepository(
  snapshot: WireChatSnapshot,
  serverMessages: readonly MoldyMessage[],
): ExportedMessageRepository {
  const records = ownedRecords(snapshot)
  const roots = new Map(records.filter((r) => !r.childId).map((r) => [r.message.id, r]))
  const valid = rootedIds(roots)
  const visible = records.filter(
    (r) => (r.childId || valid.has(r.message.id)) && !isReplayed(r, snapshot),
  )
  const serverRows = serverMessages.filter((row) => row.conversation_id === snapshot.threadId)
  const byPublicId = new Map(serverRows.map((row) => [row.id, row]))
  const cardGroups = new Map<string | undefined, AgUiActivitySnapshot[]>()
  for (const activity of snapshot.activities) {
    if (activity.activityType === 'moldy.ui_data')
      addToGroup(cardGroups, activity.subagentRunId, activity)
  }
  const groups = new Map<string | undefined, SavedRecord[]>()
  for (const record of visible) addToGroup(groups, record.childId, record)

  const rootMessages: ThreadMessage[] = []
  const childRecords: { subagentRunId: string; message: Message }[] = []
  for (const [childId, group] of groups) {
    const extras = scopedExtras(group, cardGroups.get(childId) ?? [])
    for (const record of group) {
      if (record.message.role !== 'user' && record.message.role !== 'assistant') continue
      const extra = extras.get(record.message.id) ?? []
      if (childId) {
        childRecords.push(
          ...[record.message, ...extra].map((message) => ({ subagentRunId: childId, message })),
        )
      } else {
        const row = record.link.uiMessageId ? byPublicId.get(record.link.uiMessageId) : undefined
        rootMessages.push(
          restoredRoot(record, extra, row?.role === record.message.role ? row : undefined),
        )
      }
    }
  }

  const visibleIds = new Set(rootMessages.map((message) => message.id))
  function visibleAncestor(id: string | null): string | null {
    while (id && valid.has(id) && !visibleIds.has(id))
      id = roots.get(id)?.link.parentMessageId ?? null
    return id && visibleIds.has(id) ? id : null
  }
  let headId = visibleAncestor(snapshot.activeTipMessageId)
  // assistant-ui treats a null head on a nonempty repository as its last item.
  // An absent or unresolvable selected tip must not open a different branch.
  if (!headId && (rootMessages.length || snapshot.activeTipMessageId)) {
    return { headId: null, messages: [] }
  }
  const nested = nestAgUiHistoryChildren(rootMessages, childRecords, snapshot.subagents)
  const decorated = applyMoldyActivitiesToHistory(
    nested,
    snapshot.activities,
    serverRows,
    snapshot.messageLinks,
  )
  const restored = restoreAgUiInterrupts(decorated, snapshot.interrupts, {
    runId: snapshot.interruptRunId,
    headId,
    children: snapshot.subagents,
  })
  const messages = restored.map((message) => {
    const parentId = visibleAncestor(roots.get(message.id)?.link.parentMessageId ?? null)
    if (!visibleIds.has(message.id) && message.metadata.custom.moldyInterruptParentId === headId) {
      const pendingParent = headId
      headId = message.id
      return { message, parentId: pendingParent }
    }
    return { message, parentId }
  })
  return { headId, messages }
}
