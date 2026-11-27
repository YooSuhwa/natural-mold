import type { ExportedMessageRepository, ThreadHistoryAdapter } from '@assistant-ui/react'
import { ApiError } from '@/lib/api/errors'
import type { RunActivity } from '@/lib/chat/langgraph-runtime/activity-types'
import {
  selectDeepAgentsState,
  type DeepAgentsStateSnapshot,
} from '@/lib/chat/langgraph-runtime/deepagents-state'
import {
  parsePartialModelOutput,
  type PartialModelOutput,
} from '@/lib/chat/langgraph-runtime/partial-model-output'
import { recoveryErrorCode, type RecoveryErrorCode } from '@/lib/chat-runs/recovery-error'
import type { Message as MoldyMessage } from '@/lib/types'
import { anySignal } from '@/lib/org-scope/org-scope-signal'
import { restoreAgUiNativeActivities } from './activity-native-restore'
import { rebindAgUiNativeActivities, reduceAgUiNativeActivities } from './activity-native-summary'
import type { AgUiHistoryChild } from './chat-history-children'
import {
  parsePublicActivities,
  parsePublicInterrupts,
  type AgUiActivitySnapshot,
  type AgUiMessageLink,
  type WireChatSnapshot,
} from './chat-history-types'
import { buildAgUiChatRepository } from './chat-projection'

export interface AgUiChatSessionState {
  readonly snapshot: WireChatSnapshot | null
  readonly activities: readonly AgUiActivitySnapshot[]
  readonly nativeActivities: readonly RunActivity[]
  readonly liveActivityIds: ReadonlySet<string>
  readonly subagents: readonly AgUiHistoryChild[]
  readonly messageLinks: readonly AgUiMessageLink[]
  readonly deepAgentsState: DeepAgentsStateSnapshot
  readonly interrupts: WireChatSnapshot['interrupts']
  readonly activeRunId: string | null
  readonly physicalRunId: string | null
  readonly readDenied: boolean
  readonly reconcileSequence: number
  readonly attachBlockedRunId: string | null
  readonly terminalSequence: number
  readonly terminalRunId: string | null
  readonly followupRunId: string | null
  readonly lastTerminalPhysicalRunId: string | null
  readonly failedPhysicalRunId: string | null
  readonly partialOutput?: PartialModelOutput | null
  readonly recoveryError?: RecoveryErrorCode
}

interface SessionOptions {
  readonly conversationId: string
  readonly readSnapshot: (
    signal: AbortSignal,
    isCurrent: () => boolean,
  ) => Promise<WireChatSnapshot>
  readonly getServerMessages: () => readonly MoldyMessage[]
}

const initialState = (): AgUiChatSessionState => ({
  snapshot: null,
  activities: [],
  nativeActivities: [],
  liveActivityIds: new Set<string>(),
  subagents: [],
  messageLinks: [],
  deepAgentsState: { todos: [], files: [] },
  interrupts: [],
  activeRunId: null,
  physicalRunId: null,
  readDenied: false,
  reconcileSequence: 0,
  attachBlockedRunId: null,
  terminalSequence: 0,
  terminalRunId: null,
  followupRunId: null,
  lastTerminalPhysicalRunId: null,
  failedPhysicalRunId: null,
})

function record(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
}

function id(value: unknown): string | undefined {
  return typeof value === 'string' && value.length > 0 ? value : undefined
}

function publicTodos(value: unknown): DeepAgentsStateSnapshot {
  const todos = record(value) && Array.isArray(value.todos) ? value.todos : []
  return selectDeepAgentsState({
    todos: todos.filter((todo: unknown) => record(todo) && typeof todo.content === 'string'),
  })
}

export class SupersededHistoryRead extends DOMException {
  constructor() {
    super('The chat history read was superseded.', 'AbortError')
  }
}

/** The wire adapter is read-only. Only the run API can persist or advance a conversation. */
export function createAgUiChatSession({
  conversationId,
  readSnapshot,
  getServerMessages,
}: SessionOptions) {
  let state = initialState()
  let disposed = false
  let lastResetRunId: string | null = null
  let locallyAcceptedId: string | null = null
  let readGeneration = 0
  let readAccess = new AbortController()
  const listeners = new Set<() => void>()
  const unclaimedActivities = new Set<AgUiActivitySnapshot>()
  const assistantByLane = new Map<string | undefined, string>()
  const toolOwnersByLane = new Map<string | undefined, Map<string, string>>()

  function publish(update: Partial<AgUiChatSessionState>) {
    if (disposed) return
    state = { ...state, ...update }
    for (const listener of listeners) listener()
  }

  function resetEventOwners() {
    unclaimedActivities.clear()
    assistantByLane.clear()
    toolOwnersByLane.clear()
  }

  function invalidateApproval() {
    if (state.interrupts.length)
      publish({ interrupts: [], reconcileSequence: state.reconcileSequence + 1 })
  }

  function invalidateHistoryRead() {
    // A new command or branch makes an older response ineligible to update the visible thread.
    readGeneration++
  }

  function cancelHistoryReads() {
    invalidateHistoryRead()
    readAccess.abort()
    readAccess = new AbortController()
  }

  function revokeRead() {
    locallyAcceptedId = null
    cancelHistoryReads()
    resetEventOwners()
    publish({
      ...initialState(),
      readDenied: true,
      terminalSequence: state.terminalSequence,
      reconcileSequence: state.reconcileSequence,
    })
  }

  async function refreshRepository({
    signal,
  }: { signal?: AbortSignal } = {}): Promise<ExportedMessageRepository> {
    if (disposed) throw new SupersededHistoryRead()
    const generation = ++readGeneration
    const readSignal = signal ? anySignal([signal, readAccess.signal]) : readAccess.signal
    const isCurrent = () => generation === readGeneration && !readSignal.aborted && !disposed
    try {
      readSignal.throwIfAborted()
      const snapshot = await readSnapshot(readSignal, isCurrent)
      readSignal.throwIfAborted()
      if (!isCurrent()) throw new SupersededHistoryRead()
      if (snapshot.threadId !== conversationId) throw new Error('Chat history thread mismatch.')
      const repository = buildAgUiChatRepository(snapshot, getServerMessages())
      const activities = snapshot.activities
      const failedId = snapshot.failedPhysicalRunId ?? null
      const visibleIds = new Set(activities.map((entry) => entry.messageId))
      const retainFresh = new Set(
        [...state.liveActivityIds].filter((entry) => visibleIds.has(entry)),
      )
      resetEventOwners()
      publish({
        snapshot,
        followupRunId:
          state.followupRunId &&
          !snapshot.activeRunId &&
          (snapshot.runId === state.followupRunId ||
            (snapshot.latestPhysicalRunId &&
              snapshot.latestPhysicalRunId === state.lastTerminalPhysicalRunId))
            ? state.followupRunId
            : null,
        readDenied: false,
        attachBlockedRunId:
          snapshot.activeRunId === state.attachBlockedRunId ? state.attachBlockedRunId : null,
        activities,
        nativeActivities: restoreAgUiNativeActivities(snapshot),
        liveActivityIds: retainFresh,
        subagents: snapshot.subagents,
        messageLinks: snapshot.messageLinks,
        deepAgentsState: publicTodos(snapshot.state),
        interrupts: snapshot.interrupts,
        activeRunId: snapshot.activeRunId,
        physicalRunId: snapshot.physicalRunId,
        lastTerminalPhysicalRunId: snapshot.activeRunId
          ? null
          : (snapshot.latestPhysicalRunId ?? snapshot.physicalInterruptRunId ?? failedId ?? null),
        failedPhysicalRunId: failedId,
        partialOutput:
          failedId && !snapshot.errorCode
            ? (snapshot.partialOutput ?? null)
            : snapshot.partialOutput,
        recoveryError: recoveryErrorCode(snapshot.errorCode),
      })
      return repository
    } catch (error) {
      readSignal.throwIfAborted()
      if (!isCurrent()) throw new SupersededHistoryRead()
      if (
        error instanceof ApiError &&
        (error.status === 401 || error.status === 403 || error.status === 404)
      ) {
        revokeRead()
      }
      throw error
    }
  }

  const history: ThreadHistoryAdapter = {
    scopeId: conversationId,
    async load() {
      // The lifecycle attaches through GET explicitly. Enabling implicit resume
      // here would race it and can make an SDK without connect start a new run.
      for (;;) {
        try {
          return { ...(await refreshRepository()), unstable_resume: false }
        } catch (error) {
          // The SDK will import this result and reports every rejection as a load failure.
          // If the branch changed during its first GET, finish loading the current branch.
          if (!(error instanceof SupersededHistoryRead) || disposed || state.readDenied) throw error
        }
      }
    },
    async append() {
      // assistant-ui also notifies history about transient UI messages. The
      // worker's persisted events and checkpoints already own those records.
    },
  }

  function onRunAccepted({
    runId,
    physicalRunId,
  }: {
    runId: string
    physicalRunId: string | null
  }) {
    if (disposed || state.readDenied) return
    invalidateHistoryRead()
    locallyAcceptedId = runId
    if (!physicalRunId) return
    if (state.activeRunId === runId && state.physicalRunId === physicalRunId) return
    selectRun(runId, physicalRunId)
  }

  function selectRun(runId: string, physicalRunId: string | null) {
    resetEventOwners()
    publish({
      activeRunId: runId,
      physicalRunId,
      lastTerminalPhysicalRunId: null,
      attachBlockedRunId: null,
      nativeActivities: [],
      followupRunId: null,
      liveActivityIds: new Set(),
      interrupts: [],
      failedPhysicalRunId: null,
      partialOutput: undefined,
      recoveryError: undefined,
    })
  }

  /** A queued acceptance may acquire its physical execution only after its stream opens. */
  function onRunClaimed({ runId, physicalRunId }: { runId: string; physicalRunId: string }) {
    if (
      disposed ||
      state.readDenied ||
      state.activeRunId !== runId ||
      state.physicalRunId ||
      !physicalRunId
    )
      return
    const activities = state.activities.map((entry) =>
      unclaimedActivities.has(entry) ? { ...entry, physicalRunId } : entry,
    )
    unclaimedActivities.clear()
    publish({
      physicalRunId,
      activities,
      nativeActivities: rebindAgUiNativeActivities(state.nativeActivities, runId, physicalRunId),
    })
  }

  function applyActivity(event: Record<string, unknown>, source: 'run' | 'attach') {
    const [activity] = parsePublicActivities([event])
    if (!activity) return
    const lane = activity.subagentRunId
    if (lane && !state.subagents.some((child) => child.subagentRunId === lane)) return
    const bound =
      activity.activityType === 'moldy.mcp_app' ||
      (activity.activityType === 'moldy.ui_data' && id(activity.content.tool_call_id))
    const toolId = id(activity.content.public_tool_call_id)
    // The private RPC ID and the SDK tool part ID belong to different namespaces.
    if (bound && !toolId) return
    const toolOwner = toolId ? toolOwnersByLane.get(lane)?.get(toolId) : undefined
    const messageId = activity.parentMessageId ?? id(activity.content.public_message_id)
    if (toolOwner && messageId && toolOwner !== messageId) return
    const owner =
      toolOwner ??
      messageId ??
      (bound || activity.activityType === 'moldy.subagent_todos'
        ? undefined
        : assistantByLane.get(lane))
    const scoped: AgUiActivitySnapshot = {
      ...activity,
      ...(owner && { parentMessageId: owner }),
      ...(state.physicalRunId && { physicalRunId: state.physicalRunId }),
    }
    const index = state.activities.findIndex(
      (item) =>
        item.messageId === scoped.messageId &&
        item.activityType === scoped.activityType &&
        item.subagentRunId === scoped.subagentRunId &&
        item.physicalRunId === scoped.physicalRunId,
    )
    const activities = [...state.activities]
    if (index < 0) activities.push(scoped)
    else {
      unclaimedActivities.delete(activities[index])
      activities[index] = scoped
    }
    if (!state.physicalRunId) unclaimedActivities.add(scoped)
    const liveActivityIds =
      source === 'run'
        ? new Set([...state.liveActivityIds, scoped.messageId])
        : state.liveActivityIds
    publish({ activities, liveActivityIds })
  }

  function startChild(event: Record<string, unknown>) {
    const childId = id(event.subagentRunId)
    const parentToolCallId = id(event.parentToolCallId)
    const name = id(event.name)
    if (!childId || !parentToolCallId || !name) return
    const parentSubagentRunId = id(event.parentSubagentRunId)
    if (
      parentSubagentRunId &&
      !state.subagents.some((entry) => entry.subagentRunId === parentSubagentRunId)
    )
      return
    const previous = state.subagents.find((entry) => entry.subagentRunId === childId)
    if (
      previous &&
      (previous.parentToolCallId !== parentToolCallId ||
        previous.parentSubagentRunId !== parentSubagentRunId)
    )
      return
    const parentMessageId =
      id(event.parentMessageId) ?? toolOwnersByLane.get(parentSubagentRunId)?.get(parentToolCallId)
    const child: AgUiHistoryChild = {
      ...previous,
      subagentRunId: childId,
      parentToolCallId,
      name,
      status: 'running',
      interruptIds: undefined,
      ...(parentSubagentRunId && { parentSubagentRunId }),
      ...(parentMessageId && { parentMessageId }),
      ...(id(event.description) && { description: id(event.description) }),
    }
    const subagents = previous
      ? state.subagents.map((entry) => (entry === previous ? child : entry))
      : [...state.subagents, child]
    publish({ subagents })
  }

  function finishChild(event: Record<string, unknown>) {
    const index = state.subagents.findIndex((child) => child.subagentRunId === event.subagentRunId)
    if (index < 0) return
    const child = state.subagents[index]
    const outcome = record(event.outcome) ? event.outcome : undefined
    const subagents = [...state.subagents]
    const suspended = outcome?.type === 'suspended'
    subagents[index] = {
      ...child,
      status: event.type === 'SUBAGENT_ERROR' ? 'failed' : suspended ? 'interrupted' : 'completed',
      ...(typeof event.result === 'string' && { result: event.result }),
      ...(suspended &&
        Array.isArray(outcome?.interruptIds) && {
          interruptIds: outcome.interruptIds.filter(
            (entry): entry is string => typeof entry === 'string',
          ),
        }),
    }
    publish({ subagents })
  }

  function onPublicEvent(
    event: unknown,
    { source, runId: readerRunId }: { source: 'run' | 'attach'; runId?: string },
  ) {
    if (disposed || state.readDenied || !record(event)) return
    if (event.threadId !== undefined && event.threadId !== conversationId) return
    // Content, activities and RUN_ERROR do not have a runId of their own.
    // Ignore buffered events from a reader displaced by another command/branch.
    if (readerRunId && event.runId && event.runId !== readerRunId) return
    if (readerRunId && !state.activeRunId && state.terminalRunId === readerRunId) return
    if (event.type === 'RUN_STARTED') {
      const runId = id(event.runId)
      if (!runId) return
      const localStart = source === 'run' && runId === locallyAcceptedId
      if (state.activeRunId && runId !== state.activeRunId && !localStart) return
      invalidateHistoryRead()
      if (state.activeRunId !== runId) selectRun(runId, null)
      else resetEventOwners()
      return
    }
    if (readerRunId && state.activeRunId && state.activeRunId !== readerRunId) return
    const lane = id(event.subagentRunId)
    // The command POST streams from the same authorized reader as a later GET.
    if (event.type === 'RUN_ERROR') {
      if (event.code === 'AG_UI_READ_DENIED') {
        revokeRead()
        return
      }
      if (event.code === 'AG_UI_SNAPSHOT_REQUIRED') {
        const runId = state.activeRunId
        if (!runId) return
        invalidateHistoryRead()
        if (lastResetRunId === runId) publish({ attachBlockedRunId: runId })
        else {
          lastResetRunId = runId
          publish({ reconcileSequence: state.reconcileSequence + 1 })
        }
        return
      }
    }
    // Keep early tool and child progress while a locally accepted queue entry
    // is being bound to its physical run by an authorized snapshot GET.
    const summaryRunId = state.physicalRunId ?? state.activeRunId
    if (summaryRunId && (!event.runId || state.activeRunId === event.runId)) {
      const nativeActivities = reduceAgUiNativeActivities(
        state.nativeActivities,
        event,
        summaryRunId,
      )
      if (nativeActivities !== state.nativeActivities) publish({ nativeActivities })
    }
    switch (event.type) {
      case 'RUN_FINISHED':
      case 'RUN_ERROR': {
        const runId = id(event.runId) ?? state.activeRunId
        if (!runId || lane || runId !== state.activeRunId) return
        invalidateHistoryRead()
        const failed = event.type === 'RUN_ERROR'
        const meta =
          record(event.metadata) && record(event.metadata.moldy) ? event.metadata.moldy : {}
        const outcome = record(event.outcome) ? event.outcome : undefined
        publish({
          terminalSequence: state.terminalSequence + 1,
          terminalRunId: runId,
          followupRunId:
            !failed &&
            source === 'run' &&
            locallyAcceptedId === runId &&
            outcome?.type === 'success'
              ? runId
              : null,
          lastTerminalPhysicalRunId: state.physicalRunId,
          failedPhysicalRunId: failed ? state.physicalRunId : null,
          recoveryError: failed ? recoveryErrorCode(event.code) : undefined,
          partialOutput:
            failed && event.code === 'partial_model_stream'
              ? parsePartialModelOutput(meta.partialOutput)
              : failed && !recoveryErrorCode(event.code)
                ? null
                : undefined,
          activeRunId: null,
          physicalRunId: null,
          interrupts:
            outcome?.type === 'interrupt' ? parsePublicInterrupts(outcome.interrupts) : [],
        })
        return
      }
    }
    if (!state.activeRunId) return
    switch (event.type) {
      case 'SUBAGENT_STARTED':
        return startChild(event)
      case 'SUBAGENT_ERROR':
      case 'SUBAGENT_FINISHED':
        return finishChild(event)
      case 'TEXT_MESSAGE_START': {
        const messageId = id(event.messageId)
        if (messageId && (!event.role || event.role === 'assistant'))
          assistantByLane.set(lane, messageId)
        return
      }
      case 'TOOL_CALL_START': {
        const owner = id(event.parentMessageId) ?? assistantByLane.get(lane)
        const toolId = id(event.toolCallId)
        if (!owner || !toolId) return
        const scoped = toolOwnersByLane.get(lane) ?? new Map<string, string>()
        scoped.set(toolId, owner)
        toolOwnersByLane.set(lane, scoped)
        return
      }
      case 'ACTIVITY_SNAPSHOT':
        return applyActivity(event, source)
      case 'STATE_SNAPSHOT':
        if (!lane) publish({ deepAgentsState: publicTodos(event.snapshot) })
        return
    }
  }

  return {
    history,
    getSnapshot: () => state,
    subscribe(listener: () => void) {
      if (!disposed) listeners.add(listener)
      return () => {
        listeners.delete(listener)
      }
    },
    onRunAccepted,
    onRunClaimed,
    onPublicEvent,
    refreshRepository,
    invalidateApproval,
    invalidateHistoryRead,
    cancelHistoryReads,
    dispose() {
      disposed = true
      readAccess.abort()
      resetEventOwners()
      listeners.clear()
      state = initialState()
    },
  }
}

export type AgUiChatSession = ReturnType<typeof createAgUiChatSession>
