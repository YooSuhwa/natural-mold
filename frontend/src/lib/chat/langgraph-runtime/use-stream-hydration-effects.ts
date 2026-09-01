'use client'

import { useCallback, useEffect, useRef, useState } from 'react'
import type { BaseMessage } from '@langchain/core/messages'
import { MOLDY_BRANCH_SWITCHED_EVENT, isMoldyBranchSwitchedEvent } from './branch-switch-events'
import type { PendingEditRenderState, PendingReloadRenderState } from './stream-edit-reload-types'
import { postRunHydrationIsReady } from './stream-message-comparison'
import {
  messagesFromThreadState,
  type ReloadRunCorrelation,
} from './stream-thread-state-projection'
import { loadServerThreadState } from './thread-state-checkpoints'
import { reportRuntimeFailure } from './runtime-warning'
import {
  editHydrationReady,
  reloadHydrationReady,
  useOperationHydration,
  type PendingHydrationOperation,
} from './use-operation-hydration'

interface HydrationOptions {
  readonly replaceMessages?: boolean
}

const PERSISTED_CONVERSATION_ID_PATTERN =
  /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i
const RETRY_MS = 150
const TIMEOUT_MS = 10_000

export function useStreamHydrationEffects({
  conversationId,
  activateTransportHydration,
  streamLoading,
  pendingEdit,
  pendingReload,
  reloadRunCorrelation,
  handleThreadState,
  clearServerHydrationState,
  clearPendingEdit,
  clearPendingReload,
  latestVisibleMessagesRef,
}: {
  readonly conversationId: string
  readonly activateTransportHydration: () => void
  readonly streamLoading: boolean
  readonly pendingEdit: PendingHydrationOperation<PendingEditRenderState> | null
  readonly pendingReload: PendingHydrationOperation<PendingReloadRenderState> | null
  readonly reloadRunCorrelation: ReloadRunCorrelation
  readonly handleThreadState: (state: unknown, options?: HydrationOptions) => void
  readonly clearServerHydrationState: () => void
  readonly clearPendingEdit: (conversationId: string, attemptId?: number) => void
  readonly clearPendingReload: (conversationId: string, attemptId?: number) => void
  readonly latestVisibleMessagesRef: { readonly current: readonly BaseMessage[] }
}) {
  const [pending, setPending] = useState(false)
  const wasLoadingRef = useRef(false)
  const canceledRef = useRef(false)
  const settle = useCallback(() => {
    wasLoadingRef.current = false
    setPending(false)
  }, [])
  const cancel = useCallback(() => {
    canceledRef.current = true
    settle()
  }, [settle])

  useEffect(() => {
    if (!PERSISTED_CONVERSATION_ID_PATTERN.test(conversationId)) return undefined
    let active = true
    void loadServerThreadState(conversationId)
      .then((state) => active && handleThreadState(state))
      .catch((caught: unknown) => {
        if (active) reportRuntimeFailure(caught, 'initial_hydration_failed')
      })
    return () => {
      active = false
    }
  }, [conversationId, handleThreadState])
  useEffect(activateTransportHydration, [activateTransportHydration])
  useEffect(() => {
    let active = true
    const onBranchSwitched = (event: Event): void => {
      if (!isMoldyBranchSwitchedEvent(event) || event.detail.conversationId !== conversationId)
        return
      void loadServerThreadState(conversationId)
        .then((state) => active && handleThreadState(state, { replaceMessages: true }))
        .catch((caught: unknown) => {
          if (active) reportRuntimeFailure(caught, 'branch_hydration_failed')
        })
    }
    window.addEventListener(MOLDY_BRANCH_SWITCHED_EVENT, onBranchSwitched)
    return () => {
      active = false
      window.removeEventListener(MOLDY_BRANCH_SWITCHED_EVENT, onBranchSwitched)
    }
  }, [conversationId, handleThreadState])
  useEffect(() => {
    let active = true
    queueMicrotask(() => {
      if (active) settle()
    })
    return () => {
      active = false
    }
  }, [conversationId, settle])

  useOperationHydration({
    conversationId,
    streamLoading,
    operation: pendingEdit,
    isReady: editHydrationReady,
    handleNotReadyState: true,
    reloadRunCorrelation,
    handleThreadState,
    clearServerHydrationState,
    clear: clearPendingEdit,
    failureCode: 'edit_hydration_failed',
  })
  useOperationHydration({
    conversationId,
    streamLoading,
    operation: pendingReload,
    isReady: reloadHydrationReady,
    handleNotReadyState: false,
    reloadRunCorrelation,
    handleThreadState,
    clearServerHydrationState,
    clear: clearPendingReload,
    failureCode: 'reload_hydration_failed',
  })

  useEffect(() => {
    if (streamLoading) {
      wasLoadingRef.current = true
      canceledRef.current = false
      let active = true
      queueMicrotask(() => active && setPending(false))
      return () => {
        active = false
      }
    }
    if (!wasLoadingRef.current || canceledRef.current || pendingEdit || pendingReload)
      return undefined
    let active = true
    let timer: ReturnType<typeof setTimeout> | null = null
    const startedAt = Date.now()
    queueMicrotask(() => active && setPending(true))
    const hydrate = (): void => {
      void loadServerThreadState(conversationId)
        .then((state) => {
          if (!active || canceledRef.current) return
          const messages = messagesFromThreadState(state)
          if (messages && postRunHydrationIsReady(messages, latestVisibleMessagesRef.current)) {
            handleThreadState(state, { replaceMessages: true })
            settle()
            return
          }
          handleThreadState(state)
          if (Date.now() - startedAt <= TIMEOUT_MS) timer = setTimeout(hydrate, RETRY_MS)
          else settle()
        })
        .catch((caught: unknown) => {
          if (!active || canceledRef.current) return
          if (Date.now() - startedAt > TIMEOUT_MS) {
            reportRuntimeFailure(caught, 'post_run_hydration_failed')
            settle()
          } else timer = setTimeout(hydrate, RETRY_MS)
        })
    }
    hydrate()
    return () => {
      active = false
      if (timer) clearTimeout(timer)
    }
  }, [
    conversationId,
    handleThreadState,
    latestVisibleMessagesRef,
    pendingEdit,
    pendingReload,
    settle,
    streamLoading,
  ])
  return {
    postRunHydrationPending: pending,
    cancelPostRunHydration: cancel,
    settlePostRunHydration: settle,
  }
}
