'use client'

import { CheckIcon, XIcon } from 'lucide-react'
import { cn } from '@/lib/utils'
import type { ApprovalFormState } from './use-approval-form'

type Accent = 'zinc' | 'violet'

const APPROVE_BUTTON_BY_ACCENT: Record<Accent, string> = {
  zinc:
    'bg-zinc-900 text-white hover:bg-zinc-800 dark:bg-white dark:text-zinc-900 dark:hover:bg-zinc-100',
  violet: 'bg-violet-600 text-white hover:bg-violet-700',
}

const CONTAINER_BY_ACCENT: Record<Accent, string> = {
  zinc: 'border-t border-zinc-200 px-4 py-3 dark:border-zinc-800',
  violet: 'border-t border-violet-200 px-4 py-3 dark:border-violet-800',
}

interface ApprovalFooterProps {
  form: ApprovalFormState
  /** 카드 톤. 기본 zinc, Phase 8 같은 강조 카드는 violet */
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
  accent = 'zinc',
  placeholder = '수정 의견을 입력하세요...',
  rows = 3,
  approvedStatusText = '승인되었습니다. 다음 단계로 진행합니다...',
  revisionStatusText = '수정 요청을 전달했습니다...',
  showStatusMessage = false,
}: ApprovalFooterProps) {
  const { revision, setRevision, submitted, isLocked, handleApprove, handleRevision } = form

  return (
    <div className={CONTAINER_BY_ACCENT[accent]}>
      <textarea
        value={revision}
        onChange={(e) => setRevision(e.target.value)}
        placeholder={placeholder}
        disabled={isLocked}
        rows={rows}
        className="w-full resize-none rounded-lg border border-zinc-200 bg-zinc-50 px-3 py-2 text-sm focus:border-zinc-400 focus:outline-none focus:ring-1 focus:ring-zinc-400 dark:border-zinc-700 dark:bg-zinc-800"
      />
      <div className="mt-3 flex justify-end gap-2">
        <button
          type="button"
          onClick={handleRevision}
          disabled={isLocked}
          className={cn(
            'inline-flex items-center gap-1.5 rounded-lg border border-zinc-200 px-3 py-1.5 text-sm font-medium text-zinc-700 transition hover:bg-zinc-50 disabled:cursor-not-allowed disabled:opacity-50 dark:border-zinc-700 dark:text-zinc-300 dark:hover:bg-zinc-800',
            submitted === 'revision' && 'bg-zinc-50 dark:bg-zinc-800',
          )}
        >
          <XIcon className="size-3.5" />
          수정요청
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
          승인
        </button>
      </div>
      {showStatusMessage && submitted && (
        <p className="mt-2 text-xs text-zinc-500">
          {submitted === 'approved' ? approvedStatusText : revisionStatusText}
        </p>
      )}
    </div>
  )
}
