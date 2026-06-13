'use client'

import { createContext, useContext, useEffect, useMemo, type ReactNode } from 'react'
import type { AnyStream, SubagentDiscoverySnapshot } from '@langchain/react'
import { atom, useAtomValue, useSetAtom } from 'jotai'

const EMPTY_SUBAGENTS: ReadonlyMap<string, SubagentDiscoverySnapshot> = new Map()

interface SubagentRuntimeValue {
  readonly stream: AnyStream | null
  readonly subagentsByToolCallId: ReadonlyMap<string, SubagentDiscoverySnapshot>
}

const EMPTY_VALUE = {
  stream: null,
  subagentsByToolCallId: EMPTY_SUBAGENTS,
} satisfies SubagentRuntimeValue

const SubagentRuntimeContext = createContext<SubagentRuntimeValue>(EMPTY_VALUE)
const sharedSubagentRuntimeAtom = atom<SharedSubagentRuntime | null>(null)

export interface SharedSubagentRuntime {
  readonly conversationId: string
  readonly stream: AnyStream
  readonly subagentsByToolCallId: ReadonlyMap<string, SubagentDiscoverySnapshot>
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

export function useSubagentStream(): AnyStream | null {
  return useContext(SubagentRuntimeContext).stream
}

export function useSubagentSnapshot(toolCallId: string): SubagentDiscoverySnapshot | null {
  return useContext(SubagentRuntimeContext).subagentsByToolCallId.get(toolCallId) ?? null
}

export function useSubagentSnapshots(): readonly SubagentDiscoverySnapshot[] {
  const subagentsByToolCallId = useContext(SubagentRuntimeContext).subagentsByToolCallId
  return useMemo(() => Array.from(subagentsByToolCallId.values()), [subagentsByToolCallId])
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

export function useSubagentProgressSummary(): SubagentProgressSummary {
  const snapshots = useSubagentSnapshots()
  return useMemo(() => {
    if (snapshots.length === 0) return EMPTY_PROGRESS

    let running = 0
    let completed = 0
    let failed = 0
    for (const snapshot of snapshots) {
      if (snapshot.status === 'running') running += 1
      if (snapshot.status === 'complete') completed += 1
      if (snapshot.status === 'error') failed += 1
    }

    return {
      total: snapshots.length,
      running,
      completed,
      failed,
    }
  }, [snapshots])
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
