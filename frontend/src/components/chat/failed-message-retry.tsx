'use client'

import { createContext, useCallback, useContext, useRef, useState, type ReactNode } from 'react'
import type { ChatCommandActions } from '@/lib/chat/commands/chat-command-types'
import { reportClientWarning } from '@/lib/logging/client-logger'

type RetryAction = ChatCommandActions['retryLastFailedInput']

export interface FailedMessageRetryContextValue {
  /**
   * Presence of this provider marks a recovery-aware surface. A missing current
   * failure is intentionally different from an older surface that has no
   * provider at all: its terminal failure bubbles must remain unavailable.
   */
  readonly failedRunId?: string
  readonly retryAction: RetryAction
}

type FailedMessageRetryResolution =
  | { readonly kind: 'legacy' }
  | { readonly kind: 'unavailable' }
  | { readonly kind: 'available'; readonly retryAction: NonNullable<RetryAction> }

const FailedMessageRetryContext = createContext<FailedMessageRetryContextValue | null>(null)

export function FailedMessageRetryProvider({
  children,
  value,
}: {
  readonly children: ReactNode
  readonly value: FailedMessageRetryContextValue
}) {
  return (
    <FailedMessageRetryContext.Provider value={value}>
      {children}
    </FailedMessageRetryContext.Provider>
  )
}

export function failedRunIdFromTerminalMessageId(
  messageId: string | undefined,
): string | undefined {
  const prefix = 'moldy-failed-'
  if (!messageId?.startsWith(prefix)) return undefined
  const runId = messageId.slice(prefix.length)
  return runId || undefined
}

export function useFailedMessageRetryAction(
  messageId: string | undefined,
): FailedMessageRetryResolution {
  const context = useContext(FailedMessageRetryContext)
  if (!context) return { kind: 'legacy' }
  const failedRunId = failedRunIdFromTerminalMessageId(messageId)
  if (!failedRunId || failedRunId !== context.failedRunId || !context.retryAction) {
    return { kind: 'unavailable' }
  }
  return { kind: 'available', retryAction: context.retryAction }
}

export function FailedMessageRetryButton({
  children,
  className,
  label,
  messageId,
}: {
  readonly children: ReactNode
  readonly className?: string
  readonly label: string
  readonly messageId: string | undefined
}) {
  const resolution = useFailedMessageRetryAction(messageId)
  const submittingRef = useRef(false)
  const [isSubmitting, setIsSubmitting] = useState(false)
  const retryAction = resolution.kind === 'available' ? resolution.retryAction : undefined
  const retry = useCallback(async () => {
    if (!retryAction || submittingRef.current) return
    submittingRef.current = true
    setIsSubmitting(true)
    try {
      await retryAction.execute(retryAction.failedInputId)
    } catch (error) {
      reportClientWarning('FailedMessageRetryButton', 'failed message retry was rejected', error)
    } finally {
      submittingRef.current = false
      setIsSubmitting(false)
    }
  }, [retryAction])

  if (!retryAction) return null
  return (
    <button
      type="button"
      className={className}
      aria-label={label}
      title={label}
      disabled={isSubmitting}
      onClick={() => void retry()}
    >
      {children}
    </button>
  )
}
