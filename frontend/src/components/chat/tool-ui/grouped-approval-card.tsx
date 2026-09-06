'use client'

import { useCallback, useMemo, useRef, useState, type ReactNode } from 'react'
import { CheckIcon, ShieldCheckIcon } from 'lucide-react'
import { useTranslations } from 'next-intl'
import { MultiApprovalContext, type MultiApprovalContextValue } from './multi-approval-context'

/**
 * Groups the N `request_approval` cards of ONE multi-action interrupt into a
 * single container: a "승인 대기 N건" header + a "모두 승인" (approve all) button,
 * with each action rendered below as a compact, headerless `ApprovalCard`
 * (`children`). Approving all fires every undecided card's registered approve
 * callback; the HiTL coordinator already batches the N decisions and resumes
 * once. Single-action interrupts never reach here (the group render passes them
 * through unwrapped), so the standalone card is unaffected.
 */
export function GroupedApprovalCard({ count, children }: { count: number; children: ReactNode }) {
  const t = useTranslations('chat.approval')
  // Each compact card registers its approve callback here (keyed by action index)
  // and unregisters when a row enters another decision flow, so "모두 승인"
  // only drives rows that are still eligible for automatic approval.
  const approversRef = useRef(new Map<number, () => Promise<boolean>>())
  const [resolvedActionIndexes, setResolvedActionIndexes] = useState<ReadonlySet<number>>(
    () => new Set(),
  )
  const [approvingAll, setApprovingAll] = useState(false)
  const remainingCount = Math.max(count - resolvedActionIndexes.size, 0)
  const allActionsCompleted = remainingCount === 0

  const contextValue = useMemo<MultiApprovalContextValue>(
    () => ({
      register: (idx, approve) => {
        approversRef.current.set(idx, approve)
      },
      unregister: (idx) => {
        approversRef.current.delete(idx)
      },
      resolve: (idx) => {
        setResolvedActionIndexes((current) => {
          if (current.has(idx)) return current
          return new Set([...current, idx])
        })
      },
    }),
    [],
  )

  const approveAll = useCallback(async () => {
    if (approvingAll || allActionsCompleted) return
    setApprovingAll(true)
    const approvals = [...approversRef.current.entries()]
      .filter(([idx]) => !resolvedActionIndexes.has(idx))
      .map(([, approve]) => approve())
    await Promise.allSettled(approvals)
    setApprovingAll(false)
  }, [allActionsCompleted, approvingAll, resolvedActionIndexes])

  return (
    <MultiApprovalContext.Provider value={contextValue}>
      <div
        className="moldy-chat-card moldy-status-surface moldy-status-warn w-full"
        data-testid="approval-group"
        data-hitl-total-actions={String(count)}
        data-hitl-pending-actions={String(remainingCount)}
      >
        <div className="flex items-center gap-2 border-b border-border/60 px-4 py-3">
          <ShieldCheckIcon className="moldy-status-icon size-4" />
          <span className="text-sm font-medium">
            {allActionsCompleted
              ? t('allActionsCompleted')
              : t('pendingCount', { count: remainingCount })}
          </span>
          <button
            type="button"
            onClick={() => void approveAll()}
            disabled={approvingAll || allActionsCompleted}
            data-testid="approval-approve-all-button"
            data-variant="solid"
            className="moldy-action-pill moldy-status-success ml-auto disabled:opacity-50"
          >
            <CheckIcon className="size-3" />
            {t('approveAll')}
          </button>
        </div>
        <div className="space-y-2 p-3">{children}</div>
      </div>
    </MultiApprovalContext.Provider>
  )
}
