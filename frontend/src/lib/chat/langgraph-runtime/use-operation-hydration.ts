'use client'

import { useEffect } from 'react'
import type { PendingEditRenderState, PendingReloadRenderState } from './stream-edit-reload-types'
import {
  pendingEditHydrationIsReady,
  pendingReloadHydrationIsReady,
} from './stream-edit-reload-hydration'
import type { ReloadRunCorrelation } from './stream-thread-state-projection'
import { loadServerThreadState } from './thread-state-checkpoints'
import { reportRuntimeFailure } from './runtime-warning'

export interface PendingHydrationOperation<T> {
  readonly conversationId: string
  readonly attemptId: number
  readonly value: T
}

interface HydrationOptions {
  readonly replaceMessages?: boolean
}

const RETRY_MS = 150
const TIMEOUT_MS = 10_000

export function useOperationHydration<T>({
  conversationId,
  streamLoading,
  operation,
  isReady,
  handleNotReadyState,
  reloadRunCorrelation,
  handleThreadState,
  clearServerHydrationState,
  clear,
  failureCode,
}: {
  conversationId: string
  streamLoading: boolean
  operation: PendingHydrationOperation<T> | null
  isReady: (
    state: unknown,
    operation: PendingHydrationOperation<T>,
    reloadRunCorrelation: ReloadRunCorrelation,
  ) => boolean
  handleNotReadyState: boolean
  reloadRunCorrelation: ReloadRunCorrelation
  handleThreadState: (state: unknown, options?: HydrationOptions) => void
  clearServerHydrationState: () => void
  clear: (conversationId: string, attemptId?: number) => void
  failureCode: 'edit_hydration_failed' | 'reload_hydration_failed'
}): void {
  useEffect(() => {
    if (!operation || operation.conversationId !== conversationId || streamLoading) return undefined
    let active = true
    let timer: ReturnType<typeof setTimeout> | null = null
    const startedAt = Date.now()
    const hydrate = (): void => {
      void loadServerThreadState(conversationId)
        .then((state) => {
          if (!active) return
          if (isReady(state, operation, reloadRunCorrelation)) {
            handleThreadState(state, { replaceMessages: true })
            clear(conversationId, operation.attemptId)
          } else if (Date.now() - startedAt > TIMEOUT_MS) {
            clear(conversationId, operation.attemptId)
          } else {
            if (handleNotReadyState) handleThreadState(state)
            timer = setTimeout(hydrate, RETRY_MS)
          }
        })
        .catch((caught: unknown) => {
          if (!active) return
          reportRuntimeFailure(caught, failureCode)
          clearServerHydrationState()
          clear(conversationId, operation.attemptId)
        })
    }
    hydrate()
    return () => {
      active = false
      if (timer) clearTimeout(timer)
    }
  }, [
    clear,
    clearServerHydrationState,
    conversationId,
    failureCode,
    handleThreadState,
    handleNotReadyState,
    isReady,
    operation,
    reloadRunCorrelation,
    streamLoading,
  ])
}

export function editHydrationReady(
  state: unknown,
  operation: PendingHydrationOperation<PendingEditRenderState>,
): boolean {
  return pendingEditHydrationIsReady(state, operation.value)
}

export function reloadHydrationReady(
  state: unknown,
  operation: PendingHydrationOperation<PendingReloadRenderState>,
  reloadRunCorrelation: ReloadRunCorrelation,
): boolean {
  return pendingReloadHydrationIsReady(state, operation.value, reloadRunCorrelation)
}
