'use client'

import { useEffect, useLayoutEffect, useMemo, useSyncExternalStore } from 'react'

import { conversationRunInputsApi } from '@/lib/api/conversation-run-inputs'
import { createServerMessageQueue } from './server-message-queue'
import type { ServerMessageQueueOptions } from './server-message-queue-contract'

const CLAIM_POLL_INTERVAL_MS = 1_000

type UseServerMessageQueueOptions = Pick<
  ServerMessageQueueOptions,
  'conversationId' | 'submit' | 'onClaimedRun'
> & {
  readonly createRequestId?: () => string
  readonly pollIntervalMs?: number
}

function randomRequestId(): string {
  return crypto.randomUUID()
}

const uninitializedSubmit: ServerMessageQueueOptions['submit'] = async () => {
  throw new Error('Server message queue callbacks are not initialized')
}
const ignoreClaimedRun: ServerMessageQueueOptions['onClaimedRun'] = () => undefined

export function useServerMessageQueue({
  conversationId,
  submit,
  onClaimedRun,
  createRequestId = randomRequestId,
  pollIntervalMs = CLAIM_POLL_INTERVAL_MS,
}: UseServerMessageQueueOptions) {
  const controller = useMemo(
    () =>
      createServerMessageQueue({
        conversationId,
        api: conversationRunInputsApi,
        submit: uninitializedSubmit,
        onClaimedRun: ignoreClaimedRun,
        createRequestId: randomRequestId,
      }),
    [conversationId],
  )
  useLayoutEffect(() => {
    controller.updateCallbacks({ submit, onClaimedRun, createRequestId })
  }, [controller, createRequestId, onClaimedRun, submit])
  const snapshot = useSyncExternalStore(
    controller.subscribe,
    controller.getSnapshot,
    controller.getSnapshot,
  )

  useEffect(() => {
    void controller.refresh()
  }, [controller])

  useEffect(() => {
    if (
      snapshot.lastOperation.kind !== 'queued' &&
      snapshot.lastOperation.kind !== 'reconciling' &&
      !snapshot.items.some((item) => item.status === 'pending')
    ) {
      return
    }
    const timer = window.setInterval(() => void controller.refresh(), pollIntervalMs)
    return () => window.clearInterval(timer)
  }, [controller, pollIntervalMs, snapshot.items, snapshot.lastOperation.kind])

  return { controller, snapshot }
}
