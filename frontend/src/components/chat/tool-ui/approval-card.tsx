'use client'

import { useState, useCallback, useMemo, useRef } from 'react'
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
import { useTranslations } from 'next-intl'
import { cn } from '@/lib/utils'
import { toApprove, toEdit, toReject } from '@/lib/chat/decision-mappers'
import { useHiTL } from '@/lib/chat/hitl-context'
import { redactSensitiveRecord, redactSensitiveText } from '@/lib/chat/sensitive-display'
import { useApprovalDeadline } from '@/lib/hooks/use-approval-deadline'
import type { Decision as StandardDecision } from '@/lib/types'
import { CountdownBadge } from './countdown-badge'

interface ApprovalArgs {
  /** 승인 대상 도구명 */
  tool_name?: string
  /** 도구 실행 인자 */
  tool_args?: Record<string, unknown>
  /** 왜 승인이 필요한지 설명 */
  description?: string
  /** 메시지 (description 대체) */
  message?: string
  /** 승인 만료 timeout (초) — 미지정 시 5분 */
  timeout_seconds?: number
  /** 승인 식별자 — deadline 리셋 키로 사용 */
  approval_id?: string
  /** 표준 HiTL interrupt 내 action index */
  hitl_action_index?: number
  hitl_total_actions?: number
  hitl_interrupt_id?: string | null
  allowed_decisions?: StandardDecision['type'][]
}

type Decision = 'approved' | 'modified' | 'rejected'
const REDACTED_PLACEHOLDER = '<redacted>'

interface ApprovalResult {
  decision: Decision
  modified_args?: Record<string, unknown>
  reason?: string
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
}

function restoreRedactedPlaceholders(value: unknown, original: unknown): unknown {
  if (value === REDACTED_PLACEHOLDER) return original
  if (Array.isArray(value)) {
    const originalItems = Array.isArray(original) ? original : []
    return value.map((item, index) => restoreRedactedPlaceholders(item, originalItems[index]))
  }
  if (isRecord(value)) {
    const originalRecord = isRecord(original) ? original : {}
    return Object.fromEntries(
      Object.entries(value).map(([key, item]) => [
        key,
        restoreRedactedPlaceholders(item, originalRecord[key]),
      ]),
    )
  }
  return value
}

function restoreRedactedRecordPlaceholders(
  value: Record<string, unknown>,
  original: Record<string, unknown> | undefined,
): Record<string, unknown> {
  const restored = restoreRedactedPlaceholders(value, original ?? {})
  return isRecord(restored) ? restored : value
}

function parseApprovalResult(result: unknown): ApprovalResult | null {
  const value =
    typeof result === 'string'
      ? (() => {
          try {
            const parsed: unknown = JSON.parse(result)
            return parsed
          } catch {
            return null
          }
        })()
      : result
  if (!isRecord(value)) return null
  const decision = value.decision
  if (decision !== 'approved' && decision !== 'modified' && decision !== 'rejected') return null
  const modifiedArgs = isRecord(value.modified_args) ? value.modified_args : undefined
  const reason = typeof value.reason === 'string' ? value.reason : undefined
  return {
    decision,
    ...(modifiedArgs ? { modified_args: modifiedArgs } : {}),
    ...(reason ? { reason } : {}),
  }
}

function addApprovalResultIfSupported(
  addResult: (result: unknown) => void,
  result: ApprovalResult,
): boolean {
  try {
    addResult(result)
    return true
  } catch {
    return false
  }
}

function toDecision(
  d: Decision,
  response: ApprovalResult,
  toolName: string | undefined,
): StandardDecision | null {
  switch (d) {
    case 'approved':
      return toApprove()
    case 'modified':
      // edited_action.name은 미들웨어가 ToolCall로 재발행하므로 비워서 보낼 수 없다.
      if (!toolName) return null
      return toEdit({ name: toolName, args: response.modified_args ?? {} })
    case 'rejected':
      return toReject(response.reason)
  }
}

function useDecisionStyles() {
  const t = useTranslations('chat.approval')
  return {
    approved: {
      tone: 'moldy-status-success',
      icon: CheckIcon,
      iconColor: 'moldy-status-icon',
      textColor: 'moldy-status-text',
      label: t('approved'),
    },
    modified: {
      tone: 'moldy-status-info',
      icon: PencilIcon,
      iconColor: 'moldy-status-icon',
      textColor: 'moldy-status-text',
      label: t('editApproved'),
    },
    rejected: {
      tone: 'moldy-status-danger',
      icon: XIcon,
      iconColor: 'moldy-status-icon',
      textColor: 'moldy-status-text',
      label: t('rejected'),
    },
  } as const
}

function ApprovalBadge({ result }: { result: unknown }) {
  const styles = useDecisionStyles()
  const parsed = parseApprovalResult(result)
  const decision = parsed?.decision ?? 'approved'
  const style = styles[decision] ?? styles.approved
  const Icon = style.icon

  return (
    <div
      className={cn(
        'moldy-status-surface moldy-status-card flex items-center gap-2 text-xs',
        style.tone,
      )}
    >
      <Icon className={cn('size-3.5 shrink-0', style.iconColor)} />
      <span className={cn('font-medium', style.textColor)}>{style.label}</span>
      {parsed?.reason && (
        <span className={cn('truncate opacity-70', style.textColor)}>— {parsed.reason}</span>
      )}
    </div>
  )
}

function ArgsPreview({ args }: { args: Record<string, unknown> }) {
  const t = useTranslations('chat.approval')
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
        <span className="font-medium">{t('args')}</span>
        <span className="text-muted-foreground">{t('argsCount', { count: entries.length })}</span>
        <ChevronDownIcon
          className={cn(
            'ml-auto size-3 text-muted-foreground transition-transform',
            expanded && 'rotate-180',
          )}
        />
      </button>
      {expanded && (
        <div className="border-t border-border/40 px-3 py-2">
          <pre className="whitespace-pre-wrap break-all font-mono moldy-ui-caption text-foreground/80">
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
    const t = useTranslations('chat.approval')
    const styles = useDecisionStyles()
    const hitl = useHiTL()
    const [decision, setDecision] = useState<Decision | null>(null)
    const [rejectReason, setRejectReason] = useState('')
    const [editedArgs, setEditedArgs] = useState('')
    const [showEdit, setShowEdit] = useState(false)
    const [submitting, setSubmitting] = useState(false)
    const [jsonError, setJsonError] = useState<string | null>(null)
    const [localResult, setLocalResult] = useState<ApprovalResult | null>(null)

    // 카드 인스턴스별 안정 키 — args.approval_id 우선, 없으면 마운트 시 생성
    const fallbackIdRef = useRef<string>(`approval-${Math.random().toString(36).slice(2)}`)
    const approvalId = args?.approval_id ?? fallbackIdRef.current

    // requires-action 상태일 때만 timer 활성
    const isPending =
      status.type !== 'complete' && status.type !== 'running' && result === undefined

    const resumeDecision = useCallback(
      async (standardDecision: StandardDecision, displayText?: string) => {
        if (typeof args?.hitl_action_index === 'number' && hitl?.registerDecision) {
          await hitl.registerDecision(
            args.hitl_action_index,
            standardDecision,
            displayText,
            args.hitl_interrupt_id,
          )
          return
        }
        await hitl?.onResumeDecisions([standardDecision], displayText)
      },
      [args?.hitl_action_index, args?.hitl_interrupt_id, hitl],
    )

    const handleDecision = useCallback(
      async (d: Decision, opts?: { reasonOverride?: string }) => {
        setDecision(d)
        setSubmitting(true)

        const reason = opts?.reasonOverride ?? (d === 'rejected' ? rejectReason : undefined)
        const response: ApprovalResult = { decision: d }
        const resumeResponse: ApprovalResult = { decision: d }

        if (reason) {
          response.reason = reason
          resumeResponse.reason = reason
        }

        if (d === 'modified' && editedArgs) {
          try {
            const parsed: unknown = JSON.parse(editedArgs)
            if (!isRecord(parsed)) throw new Error('edited args must be an object')
            response.modified_args = parsed
            resumeResponse.modified_args = restoreRedactedRecordPlaceholders(
              parsed,
              args?.tool_args,
            )
            setJsonError(null)
          } catch {
            setJsonError(t('invalidJson'))
            setSubmitting(false)
            setDecision(null)
            return
          }
        }

        const standardDecision = toDecision(d, resumeResponse, args?.tool_name)
        if (!standardDecision) {
          // edit인데 tool_name 미상 — backend가 무효 edited_action으로 거절할 것이므로 abort.
          setJsonError(t('invalidJson'))
          setSubmitting(false)
          setDecision(null)
          return
        }
        try {
          await resumeDecision(standardDecision, styles[d].label)
        } catch {
          setJsonError(t('resumeFailed'))
          setSubmitting(false)
          setDecision(null)
          return
        }
        addApprovalResultIfSupported(addResult, response)
        setLocalResult(response)
        setSubmitting(false)
      },
      [addResult, rejectReason, editedArgs, t, styles, args, resumeDecision],
    )

    // 만료 시 자동 reject — handleDecision 변동에 영향받지 않도록 ref로 보관
    const expireMessage = t('autoRejected')
    const handleExpire = useCallback(() => {
      if (submitting || decision !== null) return
      void handleDecision('rejected', { reasonOverride: expireMessage })
    }, [handleDecision, submitting, decision, expireMessage])

    const { remaining, isUrgent, formatted, extend } = useApprovalDeadline({
      approvalId,
      initialTimeoutSeconds: args?.timeout_seconds,
      onExpire: handleExpire,
      active: isPending,
    })

    const onInteract = useMemo(() => extend, [extend])

    // ── 완료 상태 ──
    const visibleResult = result ?? localResult
    if (status.type === 'complete' || visibleResult !== null) {
      return <ApprovalBadge result={visibleResult} />
    }

    // ── 로딩 상태 ──
    if (status.type === 'running') {
      return (
        <div className="moldy-chat-card flex items-center gap-2 px-3 py-2 text-xs">
          <Loader2Icon className="size-3.5 animate-spin text-primary-strong" />
          <span className="text-muted-foreground">{t('preparing')}</span>
        </div>
      )
    }

    // ── requires-action: 승인 카드 ──
    const toolName = args?.tool_name ?? t('toolCall')
    const rawDescription = args?.description ?? args?.message
    const description = rawDescription ? redactSensitiveText(rawDescription) : undefined
    const toolArgs = args?.tool_args ? redactSensitiveRecord(args.tool_args) : undefined

    return (
      <div
        className="moldy-chat-card moldy-status-surface moldy-status-warn w-full"
        // Per-action selector for multi-action HiTL interrupts: one approval card
        // renders per action_request, scoped by its index so E2E can approve each
        // independently and assert the total-action count.
        data-testid={
          typeof args?.hitl_action_index === 'number'
            ? `approval-action-${args.hitl_action_index}`
            : undefined
        }
        data-hitl-total-actions={
          typeof args?.hitl_total_actions === 'number' ? String(args.hitl_total_actions) : undefined
        }
      >
        {/* Header */}
        <div className="flex items-center gap-2 border-b border-border/60 px-4 py-3">
          <ShieldCheckIcon className="moldy-status-icon size-4" />
          <span className="text-sm font-medium">{t('approvalRequired')}</span>
          <CountdownBadge
            formatted={formatted}
            isUrgent={isUrgent}
            expired={remaining <= 0}
            label={t('expiresIn')}
            expiredLabel={t('expired')}
            className="ml-auto"
          />
        </div>

        <div className="space-y-3 p-4">
          {/* Tool name + description */}
          <div>
            <div className="mb-1 flex items-center gap-1.5">
              <WrenchIcon className="size-3 text-muted-foreground" />
              <span className="text-xs font-semibold">{toolName}</span>
            </div>
            {description && <p className="text-xs text-muted-foreground">{description}</p>}
          </div>

          {/* Args preview */}
          {toolArgs && Object.keys(toolArgs).length > 0 && <ArgsPreview args={toolArgs} />}

          {/* 거부 사유 입력 (거부 선택 시) */}
          {decision === 'rejected' && !submitting && (
            <textarea
              value={rejectReason}
              onChange={(e) => {
                setRejectReason(e.target.value)
                onInteract()
              }}
              onFocus={onInteract}
              placeholder={t('rejectReasonPlaceholder')}
              className="moldy-field-status moldy-status-danger w-full resize-none rounded-lg border bg-background px-3 py-2 text-xs outline-hidden placeholder:text-muted-foreground"
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
                  onInteract()
                }}
                onFocus={onInteract}
                placeholder={t('editArgsPlaceholder')}
                className="moldy-field-status moldy-status-info w-full resize-none rounded-lg border bg-background px-3 py-2 font-mono text-xs outline-hidden placeholder:text-muted-foreground"
                rows={4}
              />
            </>
          )}

          {jsonError && <p className="mt-1 text-xs text-destructive">{jsonError}</p>}

          {/* Action buttons */}
          {!submitting ? (
            <div className="flex items-center gap-2">
              {/* 승인 */}
              <button
                type="button"
                onClick={() => handleDecision('approved')}
                data-testid="approval-approve-button"
                data-variant="solid"
                className="moldy-action-pill moldy-status-success"
              >
                <CheckIcon className="size-3" />
                {t('approve')}
              </button>

              {/* 수정 후 승인 */}
              {!showEdit ? (
                <button
                  type="button"
                  onClick={() => {
                    setShowEdit(true)
                    setEditedArgs(toolArgs ? JSON.stringify(toolArgs, null, 2) : '{}')
                  }}
                  data-variant="outline"
                  className="moldy-action-pill moldy-status-info"
                >
                  <PencilIcon className="size-3" />
                  {t('edit')}
                </button>
              ) : (
                <button
                  type="button"
                  onClick={() => handleDecision('modified')}
                  data-variant="solid"
                  className="moldy-action-pill moldy-status-info"
                >
                  <PencilIcon className="size-3" />
                  {t('editAndApprove')}
                </button>
              )}

              {/* 거부 */}
              {decision !== 'rejected' ? (
                <button
                  type="button"
                  onClick={() => setDecision('rejected')}
                  data-variant="outline"
                  className="moldy-action-pill moldy-status-danger"
                >
                  <XIcon className="size-3" />
                  {t('reject')}
                </button>
              ) : (
                <button
                  type="button"
                  onClick={() => handleDecision('rejected')}
                  data-variant="solid"
                  className="moldy-action-pill moldy-status-danger"
                >
                  <XIcon className="size-3" />
                  {t('rejectConfirm')}
                </button>
              )}
            </div>
          ) : (
            <div className="flex items-center gap-2 text-xs text-muted-foreground">
              <Loader2Icon className="size-3 animate-spin" />
              {t('processing')}
            </div>
          )}
        </div>
      </div>
    )
  },
})
