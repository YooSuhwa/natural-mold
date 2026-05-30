'use client'

import { CheckIcon, XIcon } from 'lucide-react'
import { useTranslations } from 'next-intl'
import { cn } from '@/lib/utils'
import type { ApprovalFormState } from './use-approval-form'

type Accent = 'neutral' | 'violet'

const APPROVE_BUTTON_BY_ACCENT: Record<Accent, string> = {
  neutral: 'bg-foreground text-background hover:bg-foreground/90',
  violet: 'bg-status-accent text-white hover:bg-status-accent/90',
}

const CONTAINER_BY_ACCENT: Record<Accent, string> = {
  neutral: 'border-t border-border px-4 py-3',
  violet: 'border-t border-status-accent/30 px-4 py-3',
}

interface ApprovalFooterProps {
  form: ApprovalFormState
  /** 카드 톤. 기본 neutral, Phase 8 같은 강조 카드는 violet */
  accent?: Accent
  /** textarea placeholder */
  placeholder?: string
  /** textarea 행 수 */
  rows?: number
  /** 승인 메시지 라벨 (제출 후 표시) */
  approvedStatusText?: string
  /** 수정 요청 메시지 라벨 (제출 후 표시) */
  revisionStatusText?: string
  /** 제출 후 상태 메시지를 표시할지 */
  showStatusMessage?: boolean
}

/**
 * Phase 3/4/5/8 공통 approval footer.
 * textarea + (수정요청, 승인) 두 버튼.
 */
export function ApprovalFooter({
  form,
  accent = 'neutral',
  placeholder,
  rows = 3,
  approvedStatusText,
  revisionStatusText,
  showStatusMessage = false,
}: ApprovalFooterProps) {
  const t = useTranslations('chat.builderApproval')
  const { revision, setRevision, submitted, isLocked, handleApprove, handleRevision } = form
  const resolvedPlaceholder = placeholder ?? t('placeholder')
  const resolvedApprovedStatusText = approvedStatusText ?? t('approvedStatus')
  const resolvedRevisionStatusText = revisionStatusText ?? t('revisionStatus')

  return (
    <div className={CONTAINER_BY_ACCENT[accent]}>
      <textarea
        value={revision}
        onChange={(e) => setRevision(e.target.value)}
        placeholder={resolvedPlaceholder}
        disabled={isLocked}
        rows={rows}
        className="w-full resize-none rounded-lg border border-border bg-muted px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-ring"
      />
      <div className="mt-3 flex justify-end gap-2">
        <button
          type="button"
          onClick={handleRevision}
          disabled={isLocked}
          className={cn(
            'inline-flex items-center gap-1.5 rounded-lg border border-border px-3 py-1.5 text-sm font-medium text-foreground transition hover:bg-muted disabled:cursor-not-allowed disabled:opacity-50',
            submitted === 'revision' && 'bg-muted',
          )}
        >
          <XIcon className="size-3.5" />
          {t('requestRevision')}
        </button>
        <button
          type="button"
          onClick={handleApprove}
          disabled={isLocked}
          className={cn(
            'inline-flex items-center gap-1.5 rounded-lg px-3 py-1.5 text-sm font-medium transition disabled:cursor-not-allowed disabled:opacity-50',
            APPROVE_BUTTON_BY_ACCENT[accent],
            submitted === 'approved' && 'opacity-60',
          )}
        >
          <CheckIcon className="size-3.5" />
          {t('approve')}
        </button>
      </div>
      {showStatusMessage && submitted && (
        <p className="mt-2 text-xs text-muted-foreground">
          {submitted === 'approved' ? resolvedApprovedStatusText : resolvedRevisionStatusText}
        </p>
      )}
    </div>
  )
}
