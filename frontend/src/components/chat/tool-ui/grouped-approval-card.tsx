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
  // and unregisters once decided, so "모두 승인" only drives still-pending actions.
  const approversRef = useRef(new Map<number, () => void>())
  const [approvedAll, setApprovedAll] = useState(false)

  const contextValue = useMemo<MultiApprovalContextValue>(
    () => ({
      register: (idx, approve) => {
        approversRef.current.set(idx, approve)
      },
      unregister: (idx) => {
        approversRef.current.delete(idx)
      },
    }),
    [],
  )

  const approveAll = useCallback(() => {
    setApprovedAll(true)
    for (const approve of approversRef.current.values()) approve()
  }, [])

  return (
    <MultiApprovalContext.Provider value={contextValue}>
      <div
        className="moldy-chat-card moldy-status-surface moldy-status-warn w-full"
        data-testid="approval-group"
        data-hitl-total-actions={String(count)}
      >
        <div className="flex items-center gap-2 border-b border-border/60 px-4 py-3">
          <ShieldCheckIcon className="moldy-status-icon size-4" />
          <span className="text-sm font-medium">{t('pendingCount', { count })}</span>
          <button
            type="button"
            onClick={approveAll}
            disabled={approvedAll}
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
