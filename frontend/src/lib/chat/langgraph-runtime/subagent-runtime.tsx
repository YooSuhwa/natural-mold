'use client'

import {
  createContext,
  useContext,
  useEffect,
  useLayoutEffect,
  useMemo,
  type ReactNode,
} from 'react'
import type { AnyStream, SubagentDiscoverySnapshot } from '@langchain/react'
import { atom, useAtomValue, useSetAtom } from 'jotai'
import { useAuiState } from '@assistant-ui/react'
import { indexAgUiSubagents, type AgUiSubagentSnapshot } from '@/lib/ag-ui/chat-history-children'
import { selectAgUiChildTodos } from '@/lib/ag-ui/chat-child-todos'
import type { AgUiActivitySnapshot, AgUiMessageLink } from '@/lib/ag-ui/chat-history-types'
import { useChildTasks } from '@/lib/hooks/use-child-tasks'

const EMPTY_SUBAGENTS: ReadonlyMap<string, SubagentDiscoverySnapshot> = new Map()
const AUTO_COLLAPSE_COMPLETED_THRESHOLD = 5
const DEFAULT_MAX_LIVE_INLINE_DETAILS = 2

const EMPTY_ACTIVITIES: readonly AgUiActivitySnapshot[] = []
const EMPTY_AG_UI_SUBAGENTS: ReadonlyMap<string, AgUiSubagentSnapshot> = new Map()

interface SubagentRuntimeValue {
  readonly subagentsByRunId?: ReadonlyMap<string, AgUiSubagentSnapshot>
  readonly stream: AnyStream | null
  readonly subagentsByToolCallId: ReadonlyMap<string, SubagentDiscoverySnapshot>
}

const EMPTY_VALUE = {
  stream: null,
  subagentsByToolCallId: EMPTY_SUBAGENTS,
} satisfies SubagentRuntimeValue

const SubagentRuntimeContext = createContext<SubagentRuntimeValue>(EMPTY_VALUE)
const sharedSubagentRuntimeAtom = atom<SharedSubagentRuntime | null>(null)

export interface SharedSubagentRuntime extends SubagentRuntimeValue {
  readonly conversationId: string
}

interface SubagentRuntimeProviderProps {
  readonly children: ReactNode
  readonly stream?: AnyStream | null
}

function indexSubagents(
  stream: AnyStream | null | undefined,
): ReadonlyMap<string, SubagentDiscoverySnapshot> {
  if (!stream) return EMPTY_SUBAGENTS
  if (stream.subagents.size === 0) return EMPTY_SUBAGENTS
  return new Map(stream.subagents)
}

export function SubagentRuntimeProvider({ children, stream }: SubagentRuntimeProviderProps) {
  const subagentsByToolCallId = useMemo(() => indexSubagents(stream), [stream])
  const value = useMemo<SubagentRuntimeValue>(
    () => ({
      stream: stream ?? null,
      subagentsByToolCallId,
    }),
    [stream, subagentsByToolCallId],
  )

  return <SubagentRuntimeContext.Provider value={value}>{children}</SubagentRuntimeContext.Provider>
}

interface AgUiProviderProps {
  readonly conversationId: string
  readonly children: ReactNode
  readonly allowLegacyDetails?: boolean
  readonly activities?: readonly AgUiActivitySnapshot[]
  readonly links?: readonly AgUiMessageLink[]
}

export function AgUiSubagentRuntimeProvider(props: AgUiProviderProps) {
  if (props.allowLegacyDetails !== undefined) {
    return <AgUiChildPresentation {...props} allowLegacyDetails={props.allowLegacyDetails} />
  }
  return <AuthorizedAgUiChildren {...props} />
}

function AuthorizedAgUiChildren(props: AgUiProviderProps) {
  const { allowLegacyDetails } = useChildTasks(props.conversationId)
  return <AgUiChildPresentation {...props} allowLegacyDetails={allowLegacyDetails} />
}

function AgUiChildPresentation({
  children,
  conversationId,
  allowLegacyDetails,
  activities = EMPTY_ACTIVITIES,
  links,
}: AgUiProviderProps & { readonly allowLegacyDetails: boolean }) {
  const messages = useAuiState((state) => state.thread.messages)
  const setRuntime = useSetAtom(sharedSubagentRuntimeAtom)
  // Managed children are read separately by durable task ID after a fresh
  // authorization check. A public SDK transcript cannot grant that access.
  const projected = useMemo(
    () => (allowLegacyDetails ? indexAgUiSubagents(messages) : EMPTY_AG_UI_SUBAGENTS),
    [allowLegacyDetails, messages],
  )
  const value = useMemo<SharedSubagentRuntime>(() => {
    const subagentsByRunId = new Map<string, AgUiSubagentSnapshot>()
    const unique = new Map<string, AgUiSubagentSnapshot>()
    const seen = new Set<string>()
    for (const [runId, child] of projected) {
      const todos = selectAgUiChildTodos(activities, child, links)
      const snapshot = todos ? { ...child, todos } : child
      subagentsByRunId.set(runId, snapshot)
      if (seen.has(snapshot.id)) unique.delete(snapshot.id)
      else unique.set(snapshot.id, snapshot)
      seen.add(snapshot.id)
    }
    return { conversationId, subagentsByRunId, subagentsByToolCallId: unique, stream: null }
  }, [conversationId, projected, activities, links])
  useLayoutEffect(() => {
    setRuntime(value)
    return () => setRuntime((current) => (current === value ? null : current))
  }, [value, setRuntime])
  return <SubagentRuntimeContext.Provider value={value}>{children}</SubagentRuntimeContext.Provider>
}

export function useRequiresProjectedSubagent(): boolean {
  return useContext(SubagentRuntimeContext).subagentsByRunId !== undefined
}

export function useSubagentStream(): AnyStream | null {
  return useContext(SubagentRuntimeContext).stream
}

export function useSubagentSnapshot(
  toolCallId: string,
  parentMessageId?: string,
): SubagentDiscoverySnapshot | null {
  const runtime = useContext(SubagentRuntimeContext)
  if (!runtime.subagentsByRunId || !parentMessageId) {
    return runtime.subagentsByToolCallId.get(toolCallId) ?? null
  }
  let found: AgUiSubagentSnapshot | null = null
  for (const child of runtime.subagentsByRunId.values()) {
    if (child.id !== toolCallId || child.parentMessageId !== parentMessageId) continue
    if (found) return null
    found = child
  }
  return found
}

export function useSubagentSnapshots(
  parentMessageId?: string,
): readonly SubagentDiscoverySnapshot[] {
  const { subagentsByRunId, subagentsByToolCallId } = useContext(SubagentRuntimeContext)
  return useMemo(() => {
    if (!subagentsByRunId) return Array.from(subagentsByToolCallId.values())
    const children = Array.from(subagentsByRunId.values())
    return parentMessageId
      ? children.filter((child) => child.parentMessageId === parentMessageId)
      : children
  }, [subagentsByRunId, subagentsByToolCallId, parentMessageId])
}

export interface SubagentProgressSummary {
  readonly total: number
  readonly running: number
  readonly completed: number
  readonly failed: number
}

const EMPTY_PROGRESS = {
  total: 0,
  running: 0,
  completed: 0,
  failed: 0,
} satisfies SubagentProgressSummary

export interface SubagentInlinePolicy {
  readonly defaultExpanded: boolean
  readonly canRenderInlineDetails: boolean
  readonly overflowedLiveDetails: boolean
}

const COLLAPSED_INLINE_POLICY = {
  defaultExpanded: false,
  canRenderInlineDetails: false,
  overflowedLiveDetails: false,
} satisfies SubagentInlinePolicy

function scopedSnapshots(
  snapshots: readonly SubagentDiscoverySnapshot[],
  toolCallIds?: readonly string[],
): readonly SubagentDiscoverySnapshot[] {
  if (toolCallIds === undefined) return snapshots
  if (toolCallIds.length === 0) return []
  const allowedIds = new Set(toolCallIds)
  return snapshots.filter((snapshot) => allowedIds.has(snapshot.id))
}

export function summarizeSubagentProgress(
  snapshots: readonly SubagentDiscoverySnapshot[],
  toolCallIds?: readonly string[],
): SubagentProgressSummary {
  const scoped = scopedSnapshots(snapshots, toolCallIds)
  if (scoped.length === 0) return EMPTY_PROGRESS

  let running = 0
  let completed = 0
  let failed = 0
  for (const snapshot of scoped) {
    if (snapshot.status === 'running') running += 1
    if (snapshot.status === 'complete') completed += 1
    if (snapshot.status === 'error') failed += 1
  }

  return {
    total: scoped.length,
    running,
    completed,
    failed,
  }
}

export function getSubagentInlinePolicy(
  snapshots: readonly SubagentDiscoverySnapshot[],
  toolCallId: string,
  toolCallIds?: readonly string[],
): SubagentInlinePolicy {
  const scoped = scopedSnapshots(snapshots, toolCallIds)
  const snapshot = scoped.find((item) => item.id === toolCallId)
  if (!snapshot) return COLLAPSED_INLINE_POLICY

  if (snapshot.status === 'running') {
    const runningIndex = scoped
      .filter((item) => item.status === 'running')
      .findIndex((item) => item.id === toolCallId)
    const hasLiveSlot = runningIndex >= 0 && runningIndex < DEFAULT_MAX_LIVE_INLINE_DETAILS
    return {
      defaultExpanded: hasLiveSlot,
      canRenderInlineDetails: hasLiveSlot,
      overflowedLiveDetails: !hasLiveSlot,
    }
  }

  if (snapshot.status === 'error') {
    return {
      defaultExpanded: true,
      canRenderInlineDetails: true,
      overflowedLiveDetails: false,
    }
  }

  return {
    defaultExpanded: scoped.length < AUTO_COLLAPSE_COMPLETED_THRESHOLD,
    canRenderInlineDetails: true,
    overflowedLiveDetails: false,
  }
}

export function useSubagentProgressSummary(
  toolCallIds?: readonly string[],
  parentMessageId?: string,
): SubagentProgressSummary {
  const snapshots = useSubagentSnapshots(parentMessageId)
  return useMemo(() => summarizeSubagentProgress(snapshots, toolCallIds), [snapshots, toolCallIds])
}

export function useSubagentInlinePolicy(
  toolCallId: string,
  toolCallIds?: readonly string[],
  parentMessageId?: string,
): SubagentInlinePolicy {
  const snapshots = useSubagentSnapshots(parentMessageId)
  return useMemo(
    () => getSubagentInlinePolicy(snapshots, toolCallId, toolCallIds),
    [snapshots, toolCallId, toolCallIds],
  )
}

export function usePublishSubagentRuntime(
  conversationId: string,
  stream: AnyStream | null | undefined,
): void {
  const setRuntime = useSetAtom(sharedSubagentRuntimeAtom)
  const subagentsByToolCallId = useMemo(() => indexSubagents(stream), [stream])

  useEffect(() => {
    if (!stream) {
      setRuntime(null)
      return
    }

    setRuntime({
      conversationId,
      stream,
      subagentsByToolCallId,
    })

    return () => {
      setRuntime((current) => {
        if (current?.conversationId === conversationId && current.stream === stream) return null
        return current
      })
    }
  }, [conversationId, setRuntime, stream, subagentsByToolCallId])
}

export function useSharedSubagentRuntime(
  conversationId?: string | null,
): SharedSubagentRuntime | null {
  const runtime = useAtomValue(sharedSubagentRuntimeAtom)
  if (!runtime) return null
  if (conversationId && runtime.conversationId !== conversationId) return null
  return runtime
}
