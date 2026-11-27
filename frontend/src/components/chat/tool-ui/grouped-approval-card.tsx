'use client'

import { useCallback, useMemo, useRef, useState, type ReactNode } from 'react'
import { CheckIcon, ShieldCheckIcon } from 'lucide-react'
import { useTranslations } from 'next-intl'
import { useHiTL } from '@/lib/chat/hitl-context'
import { canRespondToAction } from '@/lib/chat/hitl-readiness'
import type { Decision } from '@/lib/types'
import { MultiApprovalContext, type MultiApprovalContextValue } from './multi-approval-context'

type GroupProps = { count: number; children: ReactNode }
type SubmittedBatch = {
  readonly generation: string
  readonly slots: readonly { interruptId?: string | null; actionIndex: number }[]
}

/**
 * Groups the N `request_approval` cards of ONE multi-action interrupt into a
 * single container: a "승인 대기 N건" header + a "모두 승인" (approve all) button,
 * with each action rendered below as a compact, headerless `ApprovalCard`
 * (`children`). Approving all fires every undecided card's registered approve
 * callback; the HiTL coordinator already batches the N decisions and resumes
 * once. Single-action interrupts never reach here (the group render passes them
 * through unwrapped), so the standalone card is unaffected.
 */
export function GroupedApprovalCard(props: GroupProps) {
  const hitl = useHiTL()
  const [submitted, setSubmitted] = useState<SubmittedBatch>()
  // A partial vote is only valid for the checkpoint that was reviewed. A fully
  // submitted group also belongs in history when another group's approval opens.
  const unrelated =
    hitl?.canRespondTo &&
    !submitted?.slots.some(({ interruptId, actionIndex }) =>
      hitl.canRespondTo?.(interruptId, actionIndex),
    )
  const current = hitl?.canRespond === false ? null : hitl?.approvalGeneration
  const generation = submitted && (!current || unrelated) ? submitted.generation : current
  return <ApprovalGroup key={generation} {...props} retainSubmitted={setSubmitted} />
}

function ApprovalGroup({
  count,
  children,
  retainSubmitted,
}: GroupProps & { retainSubmitted: (batch: SubmittedBatch | undefined) => void }) {
  const t = useTranslations('chat.approval')
  const hitl = useHiTL()
  const canRespond = hitl?.canRespond !== false
  // Each compact card registers its approve callback here (keyed by action index)
  // and unregisters when a row enters another decision flow, so "모두 승인"
  // only drives rows that are still eligible for automatic approval.
  const approversRef = useRef(new Map<number, () => Promise<boolean>>())
  const [hasApprovers, setHasApprovers] = useState(false)
  const [resolvedActionIndexes, setResolvedActionIndexes] = useState<ReadonlySet<number>>(
    () => new Set(),
  )
  const resolvedActionIndexesRef = useRef<ReadonlySet<number>>(new Set())
  const [approvingAll, setApprovingAll] = useState(false)
  const [batchError, setBatchError] = useState(false)
  const [activeActionIndex, setActiveActionIndex] = useState(0)
  const [batchGeneration, setBatchGeneration] = useState(0)
  const stagedRef = useRef(
    new Map<
      number,
      {
        readonly decision: Decision
        readonly displayText?: string
        readonly interruptId?: string | null
      }
    >(),
  )
  const remainingCount = Math.max(count - resolvedActionIndexes.size, 0)
  const allActionsCompleted = remainingCount === 0

  const submitDecision = useCallback<MultiApprovalContextValue['submitDecision']>(
    async (actionIndex, decision, displayText, interruptId) => {
      if (
        !canRespondToAction(hitl, {
          hitl_interrupt_id: interruptId,
          hitl_action_index: actionIndex,
        })
      ) {
        throw new Error('Approval is not ready')
      }
      setBatchError(false)
      stagedRef.current.set(actionIndex, { decision, displayText, interruptId })
      if (stagedRef.current.size < count) return

      const batch = [...stagedRef.current.entries()].sort(([left], [right]) => left - right)
      if (hitl?.approvalGeneration)
        retainSubmitted({
          generation: hitl.approvalGeneration,
          slots: batch.map(([actionIndex, item]) => ({
            actionIndex,
            interruptId: item.interruptId,
          })),
        })
      try {
        if (hitl?.registerDecision) {
          await Promise.all(
            batch.map(([index, item]) =>
              hitl.registerDecision?.(index, item.decision, item.displayText, item.interruptId),
            ),
          )
        } else {
          const display = batch
            .map(([, item]) => item.displayText)
            .filter((value): value is string => Boolean(value))
            .join(' | ')
          await hitl?.onResumeDecisions(
            batch.map(([, item]) => item.decision),
            display || undefined,
          )
        }
      } catch (error) {
        retainSubmitted(undefined)
        stagedRef.current.clear()
        resolvedActionIndexesRef.current = new Set()
        setResolvedActionIndexes(new Set())
        setActiveActionIndex(0)
        setBatchGeneration((value) => value + 1)
        setBatchError(true)
        throw error
      }
    },
    [count, hitl, retainSubmitted],
  )

  const contextValue = useMemo<MultiApprovalContextValue>(
    () => ({
      register: (idx, approve) => {
        approversRef.current.set(idx, approve)
        setHasApprovers(true)
      },
      unregister: (idx) => {
        approversRef.current.delete(idx)
        if (!approversRef.current.size) setHasApprovers(false)
      },
      resolve: (idx) => {
        const current = resolvedActionIndexesRef.current
        if (current.has(idx)) return
        const next = new Set([...current, idx])
        resolvedActionIndexesRef.current = next
        setResolvedActionIndexes(next)
        const nextActive = Array.from({ length: count }, (_, index) => index).find(
          (index) => !next.has(index),
        )
        if (nextActive !== undefined) setActiveActionIndex(nextActive)
      },
      submitDecision,
      isActive: (idx) => allActionsCompleted || idx === activeActionIndex,
    }),
    [activeActionIndex, allActionsCompleted, count, submitDecision],
  )

  const approveAll = useCallback(async () => {
    if (!canRespond || approvingAll || allActionsCompleted) return
    setApprovingAll(true)
    const approvals = [...approversRef.current.entries()]
      .filter(([idx]) => !resolvedActionIndexes.has(idx))
      .map(([, approve]) => approve())
    await Promise.allSettled(approvals)
    setApprovingAll(false)
  }, [allActionsCompleted, approvingAll, canRespond, resolvedActionIndexes])

  return (
    <MultiApprovalContext.Provider value={contextValue}>
      <div
        className="moldy-chat-card moldy-status-warn w-full border border-border bg-card text-foreground"
        data-testid="approval-group"
        data-hitl-total-actions={String(count)}
        data-hitl-pending-actions={String(remainingCount)}
        data-hitl-active-action={String(activeActionIndex)}
      >
        <div className="flex items-center gap-2 border-b border-border/60 px-4 py-3">
          <ShieldCheckIcon className="moldy-status-icon size-4" />
          <span className="text-sm font-medium">
            {allActionsCompleted
              ? t('allActionsCompleted')
              : t('pendingCount', { count: remainingCount })}
          </span>
          {!allActionsCompleted ? (
            <span className="moldy-ui-caption text-muted-foreground">
              {t('actionN', { index: activeActionIndex + 1, total: count })}
            </span>
          ) : null}
          <button
            type="button"
            onClick={() => void approveAll()}
            disabled={
              !canRespond ||
              ((hitl?.canRespond !== undefined || hitl?.canRespondTo !== undefined) &&
                !hasApprovers) ||
              approvingAll ||
              allActionsCompleted
            }
            data-testid="approval-approve-all-button"
            data-variant="solid"
            className="moldy-action-pill moldy-status-success ml-auto disabled:opacity-50"
          >
            <CheckIcon className="size-3" />
            {t('approveAll')}
          </button>
        </div>
        <div className="p-3">
          {batchError ? (
            <p role="alert" className="mb-3 text-xs text-destructive">
              {t('resumeFailed')}
            </p>
          ) : null}
          <div key={batchGeneration}>{children}</div>
        </div>
      </div>
    </MultiApprovalContext.Provider>
  )
}
