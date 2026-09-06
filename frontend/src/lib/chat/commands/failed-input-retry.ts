'use client'

import { useCallback, useMemo, useState } from 'react'
import type { ConversationRunInput } from '@/lib/api/conversation-run-inputs'
import type { ChatCommandActions } from './chat-command-types'

type RetryAction = NonNullable<ChatCommandActions['retryLastFailedInput']>

export function failedInputRetryAction(
  inputs: readonly ConversationRunInput[],
  failedRunId: string | undefined,
  retry: ((input: ConversationRunInput) => Promise<void>) | undefined,
): RetryAction | undefined {
  if (!failedRunId || !retry) return undefined
  const input = inputs.find(
    (candidate) =>
      candidate.run_id === failedRunId &&
      (candidate.status === 'claimed' || candidate.status === 'failed'),
  )
  if (!input) return undefined
  let submitted = false
  return {
    failedInputId: input.id,
    execute: async (failedInputId) => {
      if (failedInputId !== input.id) {
        throw new Error('Failed input identity changed before retry')
      }
      if (submitted) return
      submitted = true
      await retry(input)
    },
  }
}

export function useFailedInputRetryAction(
  inputs: readonly ConversationRunInput[],
  failedRunId: string | undefined,
  retry: ((input: ConversationRunInput) => Promise<void>) | undefined,
): RetryAction | undefined {
  const [submittedInputIds, setSubmittedInputIds] = useState<ReadonlySet<string>>(new Set())
  const retryOnce = useCallback(
    async (input: ConversationRunInput) => {
      if (!retry) return
      setSubmittedInputIds((current) => new Set(current).add(input.id))
      await retry(input)
    },
    [retry],
  )
  return useMemo(() => {
    const action = failedInputRetryAction(inputs, failedRunId, retry ? retryOnce : undefined)
    return action && !submittedInputIds.has(action.failedInputId) ? action : undefined
  }, [failedRunId, inputs, retry, retryOnce, submittedInputIds])
}
