'use client'

import { useEffect, useMemo, useState } from 'react'
import {
  isMoldyBranchSwitchedEvent,
  MOLDY_BRANCH_SWITCHED_EVENT,
} from './langgraph-runtime/branch-switch-events'
import type { PartialModelOutput } from './langgraph-runtime/partial-model-output'
import { terminalRunNoticeFromThreadState } from './langgraph-runtime/stream-thread-state-projection'
import { loadServerThreadState } from './langgraph-runtime/thread-state-checkpoints'
import { currentOrgScope, signalForOrgScope } from '@/lib/org-scope/org-scope-signal'

interface RecoveryTarget {
  readonly conversationId: string
  readonly runId: string
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null
}

/** Reuse the server's authorized projection of the selected checkpoint; do not attach a stream. */
export function useLegacyPartialOutput(
  conversationId: string | undefined,
  runId: string | undefined,
): PartialModelOutput | null {
  const target = useMemo(
    () => (conversationId && runId ? { conversationId, runId } : null),
    [conversationId, runId],
  )
  const [readback, setReadback] = useState<{
    readonly target: RecoveryTarget
    readonly output: PartialModelOutput
  } | null>(null)
  useEffect(() => {
    if (!target) return
    let request = new AbortController()
    const scope = signalForOrgScope(currentOrgScope(), '/api/conversations/')
    const clear = () => {
      request.abort()
      setReadback(null)
    }
    const load = async (checkpointId?: string) => {
      if (scope?.aborted) return
      const { signal } = request
      try {
        const state = await loadServerThreadState(target.conversationId, { signal, readOnly: true })
        if (signal.aborted || scope?.aborted) return
        if (!isRecord(state.metadata) || state.metadata.conversation_id !== target.conversationId)
          return
        if (
          checkpointId &&
          (!isRecord(state.checkpoint) || state.checkpoint.checkpoint_id !== checkpointId)
        )
          return
        const notice = terminalRunNoticeFromThreadState(state)
        const output = notice?.partialOutput
        if (notice?.id === target.runId && output && (output.text || output.tools.length > 0)) {
          setReadback({ target, output })
        }
      } catch {
        // An unavailable or unreadable snapshot provides no evidence for continuation.
      }
    }
    const switchBranch = (event: Event) => {
      if (
        !isMoldyBranchSwitchedEvent(event) ||
        event.detail.conversationId !== target.conversationId
      )
        return
      clear()
      request = new AbortController()
      void load(event.detail.checkpointId)
    }
    void load()
    window.addEventListener(MOLDY_BRANCH_SWITCHED_EVENT, switchBranch)
    scope?.addEventListener('abort', clear)
    return () => {
      request.abort()
      window.removeEventListener(MOLDY_BRANCH_SWITCHED_EVENT, switchBranch)
      scope?.removeEventListener('abort', clear)
    }
  }, [target])
  return readback !== null && readback.target === target ? readback.output : null
}
