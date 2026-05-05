'use client'

import { useState } from 'react'
import { useHiTL } from '@/lib/chat/hitl-context'

export type ApprovalDecision = 'approved' | 'revision' | null

export interface UseApprovalFormOptions {
  /** revision 입력이 비어있을 때 사용할 메시지 */
  revisionFallback?: string
  /** 승인 시 디스플레이 텍스트 */
  approveDisplay?: string
  /** status.type 'complete' 여부 */
  isComplete: boolean
}

export interface ApprovalFormState {
  revision: string
  setRevision: (value: string) => void
  submitted: ApprovalDecision
  isRunning: boolean
  isLocked: boolean
  handleApprove: () => Promise<void>
  handleRevision: () => Promise<void>
}

/** 공통 approval 폼 — revision 텍스트, submitted 결정, HiTL resume 송신. */
export function useApprovalForm(options: UseApprovalFormOptions): ApprovalFormState {
  const { revisionFallback = '수정 요청', approveDisplay = '승인', isComplete } = options

  const hitl = useHiTL()
  const [revision, setRevision] = useState('')
  const [submitted, setSubmitted] = useState<ApprovalDecision>(null)
  const isRunning = !isComplete
  const isLocked = !!submitted || !isRunning

  const handleApprove = async () => {
    if (submitted) return
    setSubmitted('approved')
    await hitl?.onResumeDecisions([{ type: 'approve' }], approveDisplay)
  }

  const handleRevision = async () => {
    if (submitted) return
    const msg = revision.trim() || revisionFallback
    setSubmitted('revision')
    await hitl?.onResumeDecisions([{ type: 'reject', message: msg }], msg)
  }

  return {
    revision,
    setRevision,
    submitted,
    isRunning,
    isLocked,
    handleApprove,
    handleRevision,
  }
}
