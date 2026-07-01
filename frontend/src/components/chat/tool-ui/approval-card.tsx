'use client'

import { useState, useCallback, useEffect, useMemo, useRef } from 'react'
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
import { useMultiApproval } from './multi-approval-context'
import {
  isSensitiveDisplayKey,
  redactSensitiveRecord,
  redactSensitiveText,
} from '@/lib/chat/sensitive-display'
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
): StandardDecision {
  switch (d) {
    case 'approved':
      return toApprove()
    case 'modified':
      // edited_action.name은 백엔드가 pending action을 positional index로 매칭해
      // 권위적으로 채운다. 도구 이름을 알면 advisory로 첨부하고, 모르면 생략한다
      // (예전엔 name이 없으면 하드 중단했지만 더 이상 필요 없다).
      return toEdit(
        toolName
          ? { name: toolName, args: response.modified_args ?? {} }
          : { args: response.modified_args ?? {} },
      )
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

/**
 * Human-readable rendering of a single arg value. Scalars are shown as-is so the
 * approver reads "report.md" not "\"report.md\""; objects/arrays fall back to
 * compact JSON (the common approval args — command, file_path, url — are scalar).
 */
function formatArgValue(value: unknown): string {
  if (typeof value === 'string') return value
  if (value === null) return 'null'
  if (typeof value === 'number' || typeof value === 'boolean') return String(value)
  return JSON.stringify(value)
}

function isScalarArg(value: unknown): boolean {
  return (
    value === null ||
    typeof value === 'string' ||
    typeof value === 'number' ||
    typeof value === 'boolean'
  )
}

// langchain's HumanInTheLoopMiddleware auto-builds the description as
// `${prefix}\n\nTool: ${name}\nArgs: ${args}`, which just repeats the card's
// header, tool-name line, and args list. Strip that boilerplate so the card
// isn't three copies of the same thing; keep only a meaningful custom prefix.
const DEFAULT_APPROVAL_DESCRIPTION_PREFIX = 'Tool execution requires approval'

function cleanApprovalDescription(raw: string | undefined): string | undefined {
  if (!raw) return undefined
  const prefix = raw.split(/\n\nTool:/)[0].trim()
  if (!prefix || prefix === DEFAULT_APPROVAL_DESCRIPTION_PREFIX) return undefined
  return prefix
}

// The headline should name the actual action being approved. `execute_in_skill`
// is a generic mechanism (and redundant with the "도구 사용 승인" header), so show
// the skill itself instead — derived from skill_directory ("/skills/docx-document"
// → "docx-document") or an explicit skill arg.
function resolveApprovalToolName(
  toolName: string | undefined,
  toolArgs: Record<string, unknown> | undefined,
): string | undefined {
  if (toolName === 'execute_in_skill' && toolArgs) {
    const dir = toolArgs.skill_directory
    if (typeof dir === 'string') {
      const skill = dir.split('/').filter(Boolean).pop()
      if (skill) return skill
    }
    const named = toolArgs.skill ?? toolArgs.skill_name
    if (typeof named === 'string' && named.trim()) return named.trim()
  }
  return toolName
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
        // Readable key/value list instead of a raw JSON dump — each arg name is a
        // label and its value renders plainly (mono only for non-scalar JSON).
        <dl className="space-y-1.5 border-t border-border/40 px-3 py-2">
          {entries.map(([key, value]) => (
            <div
              key={key}
              className="grid grid-cols-[minmax(0,8rem)_1fr] gap-x-3 gap-y-0.5 text-xs"
            >
              <dt className="truncate font-mono font-medium text-muted-foreground" title={key}>
                {key}
              </dt>
              <dd
                className={cn(
                  'min-w-0 break-words whitespace-pre-wrap text-foreground/80',
                  !isScalarArg(value) && 'font-mono',
                )}
              >
                {formatArgValue(value)}
              </dd>
            </div>
          ))}
        </dl>
      )}
    </div>
  )
}

/**
 * Field-based editor for approval args. Each value is editable in its own
 * control instead of one raw JSON blob, so a syntax error in a single field
 * can't abort the whole submit. Secret keys (`isSensitiveDisplayKey`) are
 * locked read-only as `<redacted>` — the backend restores them from the
 * checkpoint by index, so the frontend never reconstructs or leaks them.
 */
function ArgsEditor({
  value,
  onChange,
  onInteract,
}: {
  value: Record<string, unknown>
  onChange: (next: Record<string, unknown>) => void
  onInteract: () => void
}) {
  const t = useTranslations('chat.approval')
  // Non-scalar (object/array/number/boolean/null) values are edited as compact
  // JSON text; a parse failure flags only that field and keeps the last good
  // value in the draft — it never blocks submit (§ field-editor contract).
  const [jsonText, setJsonText] = useState<Record<string, string>>(() =>
    Object.fromEntries(
      Object.entries(value)
        .filter(([key, item]) => !isSensitiveDisplayKey(key) && typeof item !== 'string')
        .map(([key, item]) => [key, JSON.stringify(item)]),
    ),
  )
  const [fieldErrors, setFieldErrors] = useState<Record<string, boolean>>({})

  const entries = Object.entries(value)
  if (entries.length === 0) return null

  const updateString = (key: string, next: string) => {
    onInteract()
    onChange({ ...value, [key]: next })
  }

  const updateJson = (key: string, next: string) => {
    onInteract()
    setJsonText((prev) => ({ ...prev, [key]: next }))
    try {
      const parsed: unknown = JSON.parse(next)
      onChange({ ...value, [key]: parsed })
      setFieldErrors((prev) => ({ ...prev, [key]: false }))
    } catch {
      setFieldErrors((prev) => ({ ...prev, [key]: true }))
    }
  }

  return (
    <dl className="space-y-1.5 rounded-lg border border-border/40 bg-muted/30 px-3 py-2">
      {entries.map(([key, item]) => {
        const locked = isSensitiveDisplayKey(key)
        const isStringField = typeof item === 'string'
        return (
          <div key={key} className="space-y-1 text-xs">
            <dt className="truncate font-mono font-medium text-muted-foreground" title={key}>
              {key}
            </dt>
            <dd className="min-w-0">
              {locked ? (
                <input
                  type="text"
                  aria-label={key}
                  value={REDACTED_PLACEHOLDER}
                  readOnly
                  disabled
                  title={t('lockedSecretHint')}
                  className="moldy-field-status w-full cursor-not-allowed rounded-lg border bg-muted/50 px-2 py-1 font-mono text-xs text-muted-foreground outline-hidden"
                />
              ) : isStringField ? (
                <input
                  type="text"
                  aria-label={key}
                  value={String(value[key] ?? '')}
                  onChange={(e) => updateString(key, e.target.value)}
                  onFocus={onInteract}
                  className="moldy-field-status moldy-status-info w-full rounded-lg border bg-background px-2 py-1 text-xs outline-hidden"
                />
              ) : (
                <>
                  <input
                    type="text"
                    aria-label={key}
                    value={jsonText[key] ?? JSON.stringify(item)}
                    onChange={(e) => updateJson(key, e.target.value)}
                    onFocus={onInteract}
                    className="moldy-field-status moldy-status-info w-full rounded-lg border bg-background px-2 py-1 font-mono text-xs outline-hidden"
                  />
                  {fieldErrors[key] && (
                    <p className="mt-0.5 text-xs text-destructive">{t('invalidFieldValue')}</p>
                  )}
                </>
              )}
            </dd>
          </div>
        )
      })}
    </dl>
  )
}

export const ApprovalCard = makeAssistantToolUI<ApprovalArgs, unknown>({
  toolName: 'request_approval',
  render: function ApprovalRender({ args, result, status, addResult }) {
    const t = useTranslations('chat.approval')
    const styles = useDecisionStyles()
    const hitl = useHiTL()
    const multi = useMultiApproval()
    const [decision, setDecision] = useState<Decision | null>(null)
    const [rejectReason, setRejectReason] = useState('')
    // 수정 모드 draft — field-based editor가 키별로 편집한다. raw JSON 텍스트
    // 대신 칸별 값을 들고 있어 JSON.parse 실패로 전체 submit이 막히지 않는다.
    const [draft, setDraft] = useState<Record<string, unknown>>({})
    const [showEdit, setShowEdit] = useState(false)
    const [submitting, setSubmitting] = useState(false)
    const [resumeError, setResumeError] = useState<string | null>(null)
    const [localResult, setLocalResult] = useState<ApprovalResult | null>(null)

    // 카드 인스턴스별 안정 키 — args.approval_id 우선, 없으면 마운트 시 생성
    const fallbackIdRef = useRef<string>(`approval-${Math.random().toString(36).slice(2)}`)
    const approvalId = args?.approval_id ?? fallbackIdRef.current

    // requires-action 상태일 때만 timer 활성
    const isPending =
      status.type !== 'complete' && status.type !== 'running' && result === undefined
    // 그룹(멀티액션) 안에서 렌더될 때는 compact 모드 — 자체 헤더/카운트다운을 숨기고
    // (그룹 컨테이너가 대신 보여준다) "모두 승인"을 위해 승인 콜백을 등록한다.
    const grouped = Boolean(multi) && typeof args?.hitl_action_index === 'number'

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

        if (d === 'modified') {
          // field-based editor의 draft를 그대로 사용한다. 시크릿 칸은 잠겨
          // <redacted>로 남고, 백엔드가 checkpoint 원본으로 복원한다(프론트
          // 복원 없음). JSON.parse가 없으므로 syntax 에러로 막히지 않는다.
          response.modified_args = draft
          resumeResponse.modified_args = draft
        }

        const standardDecision = toDecision(d, resumeResponse, args?.tool_name)
        try {
          await resumeDecision(standardDecision, styles[d].label)
        } catch {
          setResumeError(t('resumeFailed'))
          setSubmitting(false)
          setDecision(null)
          return
        }
        addApprovalResultIfSupported(addResult, response)
        setLocalResult(response)
        setSubmitting(false)
      },
      [addResult, rejectReason, draft, t, styles, args, resumeDecision],
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
      // In a group the countdown badge isn't rendered, so keeping the timer would
      // silently auto-reject a card mid-decision. Disable per-card auto-expire in
      // compact mode (the group has no visible deadline).
      active: isPending && !grouped,
    })

    const onInteract = useMemo(() => extend, [extend])

    // 도구별 allowed_decisions 게이팅 — 카드가 받은 화이트리스트대로 버튼을 노출.
    // 빈/누락이면 approve+reject만(edit 제외) — standard-interrupt의 reviewForAction
    // fallback과 동일. execute_in_skill(approve,reject)엔 수정 버튼이 뜨지 않는다.
    const allowedDecisions = useMemo(
      () => new Set(args?.allowed_decisions ?? []),
      [args?.allowed_decisions],
    )
    const canApprove = allowedDecisions.size === 0 || allowedDecisions.has('approve')
    const canEdit = allowedDecisions.has('edit')
    const canReject = allowedDecisions.size === 0 || allowedDecisions.has('reject')

    // "모두 승인"을 위해 미결정 카드의 승인 콜백을 그룹 컨테이너에 등록. 결정되거나
    // (localResult) 사용자가 이미 거부/수정 흐름에 들어간 카드(decision/showEdit)는
    // 등록에서 빠져, "모두 승인"이 진행 중인 거부·수정 의도를 덮어쓰지 않는다.
    useEffect(() => {
      const idx = args?.hitl_action_index
      if (
        !grouped ||
        !multi ||
        typeof idx !== 'number' ||
        !canApprove ||
        localResult !== null ||
        decision !== null ||
        showEdit
      ) {
        return
      }
      multi.register(idx, () => void handleDecision('approved'))
      return () => multi.unregister(idx)
    }, [
      grouped,
      multi,
      canApprove,
      localResult,
      decision,
      showEdit,
      args?.hitl_action_index,
      handleDecision,
    ])

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
    const toolName = resolveApprovalToolName(args?.tool_name, args?.tool_args) ?? t('toolCall')
    const rawDescription = cleanApprovalDescription(args?.description ?? args?.message)
    const description = rawDescription ? redactSensitiveText(rawDescription) : undefined
    const toolArgs = args?.tool_args ? redactSensitiveRecord(args.tool_args) : undefined

    // Per-action selector for multi-action HiTL interrupts: one approval card
    // renders per action_request, scoped by its index so E2E can approve each
    // independently and assert the total-action count.
    const cardTestId =
      typeof args?.hitl_action_index === 'number'
        ? `approval-action-${args.hitl_action_index}`
        : undefined
    const totalActions =
      typeof args?.hitl_total_actions === 'number' ? String(args.hitl_total_actions) : undefined

    const body = (
      <div className="space-y-3 p-4">
        {/* Tool name + description */}
        <div>
          <div className="mb-1 flex items-center gap-1.5">
            <WrenchIcon className="size-3 text-muted-foreground" />
            <span className="text-xs font-semibold">{toolName}</span>
          </div>
          {description && <p className="text-xs text-muted-foreground">{description}</p>}
        </div>

        {/* Args preview — collapsed by default; the headline now names the
              actual skill/tool, so expanding is only needed to inspect details. */}
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

        {/* 수정 인자 입력 (수정 선택 시) — 칸별 field editor. 시크릿 키는
              read-only 잠금, 비-scalar는 칸별 JSON. raw JSON textarea 아님. */}
        {showEdit && !submitting && (
          <ArgsEditor value={draft} onChange={setDraft} onInteract={onInteract} />
        )}

        {resumeError && <p className="mt-1 text-xs text-destructive">{resumeError}</p>}

        {/* Action buttons */}
        {!submitting ? (
          <div className="flex items-center gap-2">
            {/* 승인 */}
            {canApprove && (
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
            )}

            {/* 수정 후 승인 — allowed_decisions에 edit이 있을 때만 노출 */}
            {canEdit &&
              (!showEdit ? (
                <button
                  type="button"
                  onClick={() => {
                    setShowEdit(true)
                    setDraft({ ...(toolArgs ?? {}) })
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
              ))}

            {/* 거부 */}
            {canReject &&
              (decision !== 'rejected' ? (
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
              ))}
          </div>
        ) : (
          <div className="flex items-center gap-2 text-xs text-muted-foreground">
            <Loader2Icon className="size-3 animate-spin" />
            {t('processing')}
          </div>
        )}
      </div>
    )

    // 그룹(멀티액션) 안: 헤더/카운트다운 없이 compact 블록. 그룹 컨테이너가
    // "승인 대기 N건" 헤더와 단일 카운트다운, "모두 승인"을 소유한다.
    if (grouped) {
      return (
        <div
          data-testid={cardTestId}
          data-hitl-total-actions={totalActions}
          className="moldy-chat-card w-full"
        >
          {body}
        </div>
      )
    }

    return (
      <div
        className="moldy-chat-card moldy-status-surface moldy-status-warn w-full"
        data-testid={cardTestId}
        data-hitl-total-actions={totalActions}
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
        {body}
      </div>
    )
  },
})
