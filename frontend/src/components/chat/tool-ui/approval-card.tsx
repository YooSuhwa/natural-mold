'use client'
// TODO: 하드코딩된 한국어 문자열을 next-intl 메시지 키로 교체

import { useState, useCallback } from 'react'
import { makeAssistantToolUI } from '@assistant-ui/react'
import {
  ShieldCheckIcon,
  CheckIcon,
  XIcon,
  PencilIcon,
  Loader2Icon,
  ChevronDownIcon,
  WrenchIcon,
} from 'lucide-react'
import { cn } from '@/lib/utils'
import { useHiTL } from '@/lib/chat/hitl-context'

interface ApprovalArgs {
  /** 승인 대상 도구명 */
  tool_name?: string
  /** 도구 실행 인자 */
  tool_args?: Record<string, unknown>
  /** 왜 승인이 필요한지 설명 */
  description?: string
  /** 메시지 (description 대체) */
  message?: string
}

type Decision = 'approved' | 'modified' | 'rejected'

interface ApprovalResult {
  decision: Decision
  modified_args?: Record<string, unknown>
  reason?: string
}

const DECISION_STYLE = {
  approved: {
    border: 'border-emerald-200 dark:border-emerald-900',
    bg: 'bg-emerald-50 dark:bg-emerald-950',
    icon: CheckIcon,
    iconColor: 'text-emerald-500',
    textColor: 'text-emerald-700 dark:text-emerald-300',
    label: '승인됨',
  },
  modified: {
    border: 'border-blue-200 dark:border-blue-900',
    bg: 'bg-blue-50 dark:bg-blue-950',
    icon: PencilIcon,
    iconColor: 'text-blue-500',
    textColor: 'text-blue-700 dark:text-blue-300',
    label: '수정 후 승인',
  },
  rejected: {
    border: 'border-red-200 dark:border-red-900',
    bg: 'bg-red-50 dark:bg-red-950',
    icon: XIcon,
    iconColor: 'text-red-500',
    textColor: 'text-red-700 dark:text-red-300',
    label: '거부됨',
  },
} as const

function ApprovalBadge({ result }: { result: unknown }) {
  const parsed = result as ApprovalResult | null
  const decision = parsed?.decision ?? 'approved'
  const style = DECISION_STYLE[decision] ?? DECISION_STYLE.approved
  const Icon = style.icon

  return (
    <div
      className={cn(
        'flex items-center gap-2 rounded-xl border px-3 py-2 text-xs',
        style.border,
        style.bg,
      )}
    >
      <Icon className={cn('size-3.5 shrink-0', style.iconColor)} />
      <span className={cn('font-medium', style.textColor)}>{style.label}</span>
      {parsed?.reason && (
        <span className={cn('truncate opacity-70', style.textColor)}>
          — {parsed.reason}
        </span>
      )}
    </div>
  )
}

function ArgsPreview({ args }: { args: Record<string, unknown> }) {
  const [expanded, setExpanded] = useState(false)
  const entries = Object.entries(args)
  if (entries.length === 0) return null

  return (
    <div className="rounded-lg border border-border/40 bg-muted/30">
      <button
        type="button"
        onClick={() => setExpanded(!expanded)}
        className="flex w-full items-center gap-2 px-3 py-2 text-left text-xs"
      >
        <WrenchIcon className="size-3 text-muted-foreground" />
        <span className="font-medium">실행 인자</span>
        <span className="text-muted-foreground">{entries.length}개</span>
        <ChevronDownIcon
          className={cn(
            'ml-auto size-3 text-muted-foreground transition-transform',
            expanded && 'rotate-180',
          )}
        />
      </button>
      {expanded && (
        <div className="border-t border-border/40 px-3 py-2">
          <pre className="whitespace-pre-wrap break-all font-mono text-[11px] text-foreground/80">
            {JSON.stringify(args, null, 2)}
          </pre>
        </div>
      )}
    </div>
  )
}

export const ApprovalCard = makeAssistantToolUI<ApprovalArgs, unknown>({
  toolName: 'request_approval',
  render: function ApprovalRender({ args, result, status, addResult }) {
    const hitl = useHiTL()
    const [decision, setDecision] = useState<Decision | null>(null)
    const [rejectReason, setRejectReason] = useState('')
    const [editedArgs, setEditedArgs] = useState('')
    const [showEdit, setShowEdit] = useState(false)
    const [submitting, setSubmitting] = useState(false)
    const [jsonError, setJsonError] = useState<string | null>(null)

    const handleDecision = useCallback(
      async (d: Decision) => {
        setDecision(d)
        setSubmitting(true)

        const response: ApprovalResult = { decision: d }

        if (d === 'rejected' && rejectReason) {
          response.reason = rejectReason
        }

        if (d === 'modified' && editedArgs) {
          try {
            response.modified_args = JSON.parse(editedArgs) as Record<
              string,
              unknown
            >
            setJsonError(null)
          } catch {
            setJsonError('유효하지 않은 JSON 형식입니다')
            setSubmitting(false)
            setDecision(null)
            return
          }
        }

        addResult(response)
        await hitl?.onResume(response, DECISION_STYLE[d].label)
      },
      [addResult, hitl, rejectReason, editedArgs],
    )

    // ── 완료 상태 ──
    if (status.type === 'complete' || result !== undefined) {
      return <ApprovalBadge result={result} />
    }

    // ── 로딩 상태 ──
    if (status.type === 'running') {
      return (
        <div className="flex items-center gap-2 rounded-xl border bg-muted/20 px-3 py-2 text-xs">
          <Loader2Icon className="size-3.5 animate-spin text-primary" />
          <span className="text-muted-foreground">승인 요청 준비 중…</span>
        </div>
      )
    }

    // ── requires-action: 승인 카드 ──
    const toolName = args?.tool_name ?? '도구'
    const description = args?.description ?? args?.message
    const toolArgs = args?.tool_args

    return (
      <div className="w-full rounded-xl border border-amber-200 bg-amber-50/50 shadow-sm dark:border-amber-900 dark:bg-amber-950/30">
        {/* Header */}
        <div className="flex items-center gap-2 border-b border-amber-200/50 px-4 py-3 dark:border-amber-900/50">
          <ShieldCheckIcon className="size-4 text-amber-600 dark:text-amber-400" />
          <span className="text-sm font-medium">승인이 필요합니다</span>
        </div>

        <div className="space-y-3 p-4">
          {/* Tool name + description */}
          <div>
            <div className="mb-1 flex items-center gap-1.5">
              <WrenchIcon className="size-3 text-muted-foreground" />
              <span className="text-xs font-semibold">{toolName}</span>
            </div>
            {description && (
              <p className="text-xs text-muted-foreground">{description}</p>
            )}
          </div>

          {/* Args preview */}
          {toolArgs && Object.keys(toolArgs).length > 0 && (
            <ArgsPreview args={toolArgs} />
          )}

          {/* 거부 사유 입력 (거부 선택 시) */}
          {decision === 'rejected' && !submitting && (
            <textarea
              value={rejectReason}
              onChange={(e) => setRejectReason(e.target.value)}
              placeholder="거부 사유를 입력하세요 (선택)"
              className="w-full resize-none rounded-lg border border-red-200 bg-background px-3 py-2 text-xs outline-none placeholder:text-muted-foreground focus:ring-1 focus:ring-red-300 dark:border-red-900"
              rows={2}
            />
          )}

          {/* 수정 인자 입력 (수정 선택 시) */}
          {showEdit && !submitting && (
            <>
              <textarea
                value={editedArgs}
                onChange={(e) => {
                  setEditedArgs(e.target.value)
                  setJsonError(null)
                }}
                placeholder="수정된 인자 (JSON)"
                className="w-full resize-none rounded-lg border border-blue-200 bg-background px-3 py-2 font-mono text-xs outline-none placeholder:text-muted-foreground focus:ring-1 focus:ring-blue-300 dark:border-blue-900"
                rows={4}
              />
              {jsonError && (
                <p className="mt-1 text-xs text-destructive">{jsonError}</p>
              )}
            </>
          )}

          {/* Action buttons */}
          {!submitting ? (
            <div className="flex items-center gap-2">
              {/* 승인 */}
              <button
                type="button"
                onClick={() => handleDecision('approved')}
                className="flex items-center gap-1.5 rounded-full bg-emerald-600 px-4 py-2 text-xs font-medium text-white transition-colors hover:bg-emerald-700"
              >
                <CheckIcon className="size-3" />
                승인
              </button>

              {/* 수정 후 승인 */}
              {!showEdit ? (
                <button
                  type="button"
                  onClick={() => {
                    setShowEdit(true)
                    setEditedArgs(
                      toolArgs ? JSON.stringify(toolArgs, null, 2) : '{}',
                    )
                  }}
                  className="flex items-center gap-1.5 rounded-full border border-blue-300 px-4 py-2 text-xs font-medium text-blue-700 transition-colors hover:bg-blue-50 dark:border-blue-800 dark:text-blue-300 dark:hover:bg-blue-950"
                >
                  <PencilIcon className="size-3" />
                  수정
                </button>
              ) : (
                <button
                  type="button"
                  onClick={() => handleDecision('modified')}
                  className="flex items-center gap-1.5 rounded-full bg-blue-600 px-4 py-2 text-xs font-medium text-white transition-colors hover:bg-blue-700"
                >
                  <PencilIcon className="size-3" />
                  수정 후 승인
                </button>
              )}

              {/* 거부 */}
              {decision !== 'rejected' ? (
                <button
                  type="button"
                  onClick={() => setDecision('rejected')}
                  className="flex items-center gap-1.5 rounded-full border border-red-300 px-4 py-2 text-xs font-medium text-red-700 transition-colors hover:bg-red-50 dark:border-red-800 dark:text-red-300 dark:hover:bg-red-950"
                >
                  <XIcon className="size-3" />
                  거부
                </button>
              ) : (
                <button
                  type="button"
                  onClick={() => handleDecision('rejected')}
                  className="flex items-center gap-1.5 rounded-full bg-red-600 px-4 py-2 text-xs font-medium text-white transition-colors hover:bg-red-700"
                >
                  <XIcon className="size-3" />
                  거부 확인
                </button>
              )}
            </div>
          ) : (
            <div className="flex items-center gap-2 text-xs text-muted-foreground">
              <Loader2Icon className="size-3 animate-spin" />
              처리 중…
            </div>
          )}
        </div>
      </div>
    )
  },
})
