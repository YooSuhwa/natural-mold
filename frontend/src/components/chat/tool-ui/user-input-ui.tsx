'use client'

import { useState, useCallback, useMemo, useRef } from 'react'
import { makeAssistantToolUI } from '@assistant-ui/react'
import { MessageSquareQuoteIcon, CheckCircle2Icon, SendIcon, Loader2Icon } from 'lucide-react'
import { useTranslations } from 'next-intl'
import { cn, toggleSetItem } from '@/lib/utils'
import { toRespond } from '@/lib/chat/decision-mappers'
import { useHiTL } from '@/lib/chat/hitl-context'
import { useApprovalDeadline } from '@/lib/hooks/use-approval-deadline'
import type { UserInputOption, UserInputQuestion } from '@/lib/types'
import { CountdownBadge } from './countdown-badge'
import { QuestionFlowCard } from './question-flow-card'
import { OptionListCard } from './option-list-card'

interface AskUserArgs {
  mode?: 'question_flow' | 'option_list'
  title?: string
  /** 복수 질문 */
  questions?: UserInputQuestion[]
  /** 단일 질문 폴백 */
  question?: string
  type?: UserInputQuestion['type']
  options?: Array<string | UserInputOption>
  minSelections?: number
  maxSelections?: number
  /** 입력 만료 timeout (초) — 미지정 시 5분 */
  timeout_seconds?: number
  /** 입력 식별자 — deadline 리셋 키로 사용 */
  approval_id?: string
  /** 표준 HiTL interrupt 내 action index */
  hitl_action_index?: number
  hitl_total_actions?: number
  hitl_interrupt_id?: string | null
  allowed_decisions?: string[]
}

type Answers = Record<number, unknown>

function SingleSelectInput({
  question,
  selected,
  onSelect,
}: {
  question: UserInputQuestion
  selected: string | null
  onSelect: (value: string) => void
}) {
  return (
    <div className="space-y-2">
      <p className="text-sm font-medium">{question.question}</p>
      <div className="flex flex-wrap gap-2">
        {question.options?.map((opt) => (
          <button
            key={opt.label}
            type="button"
            onClick={() => onSelect(opt.label)}
            className={cn(
              'rounded-full border px-3 py-1.5 text-xs transition-[background-color,border-color,color,box-shadow]',
              selected === opt.label
                ? 'border-primary bg-primary/10 text-primary-strong ring-1 ring-primary/30'
                : 'border-border hover:border-primary/50 hover:bg-accent',
            )}
          >
            {opt.label}
          </button>
        ))}
      </div>
    </div>
  )
}

function MultiSelectInput({
  question,
  selected,
  onToggle,
}: {
  question: UserInputQuestion
  selected: Set<string>
  onToggle: (value: string) => void
}) {
  return (
    <div className="space-y-2">
      <p className="text-sm font-medium">{question.question}</p>
      <div className="space-y-1.5">
        {question.options?.map((opt) => {
          const checked = selected.has(opt.label)
          return (
            <label
              key={opt.label}
              className={cn(
                'flex cursor-pointer items-center gap-2.5 rounded-lg border px-3 py-2 text-xs transition-[background-color,border-color]',
                checked ? 'border-primary/50 bg-primary/5' : 'border-border hover:bg-accent',
              )}
            >
              <input
                type="checkbox"
                checked={checked}
                onChange={() => onToggle(opt.label)}
                className="size-3.5 rounded border-border accent-primary"
              />
              <span>{opt.label}</span>
              {opt.description && <span className="text-muted-foreground">{opt.description}</span>}
            </label>
          )
        })}
      </div>
    </div>
  )
}

function TextInput({
  question,
  value,
  onChange,
  onFocus,
  placeholder,
}: {
  question: UserInputQuestion
  value: string
  onChange: (value: string) => void
  onFocus: () => void
  placeholder: string
}) {
  return (
    <div className="space-y-2">
      <p className="text-sm font-medium">{question.question}</p>
      <textarea
        value={value}
        onChange={(e) => onChange(e.target.value)}
        onFocus={onFocus}
        placeholder={placeholder}
        className="w-full resize-none rounded-lg border border-border bg-background px-3 py-2 text-sm outline-hidden transition-colors placeholder:text-muted-foreground focus:border-primary/50 focus:ring-1 focus:ring-primary/30"
        rows={3}
      />
    </div>
  )
}

function formatStructuredResult(value: unknown): string | null {
  if (typeof value !== 'object' || value === null) return null
  const payload = value as {
    mode?: unknown
    labels?: unknown
  }

  if (payload.mode === 'option_list' && Array.isArray(payload.labels)) {
    return payload.labels.map(String).join(', ')
  }

  if (payload.mode === 'question_flow' && typeof payload.labels === 'object' && payload.labels) {
    return Object.values(payload.labels)
      .map((label) => (Array.isArray(label) ? label.map(String).join(', ') : String(label)))
      .filter(Boolean)
      .join(' | ')
  }

  return null
}

export function formatUserInputResult(result: unknown): string {
  if (typeof result === 'string') {
    const trimmed = result.trim()
    if (trimmed.startsWith('{') && trimmed.endsWith('}')) {
      try {
        const parsed = JSON.parse(trimmed) as unknown
        return formatStructuredResult(parsed) ?? result
      } catch {
        return result
      }
    }
    return result
  }
  return formatStructuredResult(result) ?? JSON.stringify(result)
}

function CompletedBadge({ result }: { result: unknown }) {
  const t = useTranslations('chat.userInput')
  const display = formatUserInputResult(result)
  return (
    <div className="moldy-status-surface moldy-status-success moldy-status-card flex items-center gap-2 text-xs">
      <CheckCircle2Icon className="moldy-status-icon size-3.5 shrink-0" />
      <span className="moldy-status-text font-medium">{t('completed')}</span>
      {display && <span className="moldy-status-muted-text truncate">{display}</span>}
    </div>
  )
}

/** string[] → {label}[] 변환. 스트리밍 부분 args에선 배열이 아닐 수 있어
 * Array.isArray로 가드한다 (문자열 조각은 length>0을 통과해 map에서 크래시). */
function normalizeOptions(options?: Array<string | UserInputOption>): UserInputQuestion['options'] {
  if (!Array.isArray(options) || options.length === 0) return undefined
  return options.map((o) => (typeof o === 'string' ? { label: o } : o))
}

/** args에서 질문 배열 정규화 */
function normalizeQuestions(args: AskUserArgs): UserInputQuestion[] {
  if (Array.isArray(args.questions) && args.questions.length > 0) {
    return args.questions.map((q) => ({
      ...q,
      question: q.question ?? q.label ?? q.id ?? '',
      label: q.label ?? q.question ?? q.id,
      options: normalizeOptions(q.options),
      type: q.type ?? (q.options?.length ? 'single_select' : 'text'),
      required: q.required ?? true,
    }))
  }
  if (args.question) {
    const opts = normalizeOptions(args.options)
    return [
      {
        question: args.question,
        label: args.question,
        // options가 있으면 자동으로 single_select
        type: args.type ?? (opts?.length ? 'single_select' : 'text'),
        options: opts,
        required: true,
      },
    ]
  }
  return []
}

export const UserInputUI = makeAssistantToolUI<AskUserArgs, unknown>({
  toolName: 'ask_user',
  render: function AskUserRender({ args, result, status }) {
    const t = useTranslations('chat.userInput')
    const hitl = useHiTL()
    const [answers, setAnswers] = useState<Answers>({})
    const [submitState, setSubmitState] = useState<'idle' | 'submitting' | 'submitted'>('idle')
    const [submittedDisplay, setSubmittedDisplay] = useState<string | null>(null)

    const questions = useMemo(() => normalizeQuestions(args ?? {}), [args])
    const optionListOptions = useMemo(() => normalizeOptions(args?.options) ?? [], [args?.options])
    const submitDecision = useCallback(
      async (decision: ReturnType<typeof toRespond>, displayText?: string) => {
        if (typeof args?.hitl_action_index === 'number' && hitl?.registerDecision) {
          await hitl.registerDecision(
            args.hitl_action_index,
            decision,
            displayText,
            args.hitl_interrupt_id,
          )
          return
        }
        await hitl?.onResumeDecisions([decision], displayText)
      },
      [args?.hitl_action_index, args?.hitl_interrupt_id, hitl],
    )

    const submitResponse = useCallback(
      async (message: string, displayText: string) => {
        setSubmitState('submitting')
        setSubmittedDisplay(displayText)
        await submitDecision(toRespond(message), displayText)
        setSubmitState('submitted')
      },
      [submitDecision],
    )

    // 입력 인스턴스별 안정 키 — args.approval_id 우선, 없으면 마운트 시 생성
    const fallbackIdRef = useRef<string>(`ask-user-${Math.random().toString(36).slice(2)}`)
    const approvalId = args?.approval_id ?? fallbackIdRef.current

    // requires-action 상태일 때만 timer 활성
    const isPending =
      submitState === 'idle' &&
      result === undefined &&
      status.type !== 'complete' &&
      status.type !== 'running'

    const handleSubmit = useCallback(
      async (opts?: { skipReason?: string }) => {
        // 응답 직렬화
        const response: Record<string, unknown> = {}
        questions.forEach((q, i) => {
          const key = q.question ?? q.label ?? q.id ?? `question_${i + 1}`
          const val = answers[i]
          if (val instanceof Set) {
            response[key] = Array.from(val)
          } else {
            response[key] = val ?? ''
          }
        })

        setSubmitState('submitting')

        // 질문이 1개면 값만 전송, 복수면 객체 전송
        const payload = questions.length === 1 ? Object.values(response)[0] : response

        // 화면 표시용 텍스트
        const displayText =
          opts?.skipReason ??
          questions
            .map((_, i) => {
              const val = answers[i]
              if (val instanceof Set) return Array.from(val).join(', ')
              return String(val ?? '')
            })
            .join(' | ')

        const message = typeof payload === 'string' ? payload : JSON.stringify(payload)
        setSubmittedDisplay(displayText)
        await submitDecision(toRespond(message), displayText)
        setSubmitState('submitted')
      },
      [answers, questions, submitDecision],
    )

    // 만료 시 빈 답변으로 자동 제출 — 에이전트 graph가 무한히 paused되지 않도록
    const expireMessage = t('autoSkipped')
    const handleExpire = useCallback(() => {
      if (submitState !== 'idle') return
      void submitResponse(expireMessage, expireMessage)
    }, [submitResponse, submitState, expireMessage])

    const { remaining, isUrgent, formatted, extend } = useApprovalDeadline({
      approvalId,
      initialTimeoutSeconds: args?.timeout_seconds,
      onExpire: handleExpire,
      active: isPending,
    })

    const updateAnswer = useCallback(
      (idx: number, value: unknown) => {
        setAnswers((prev) => ({ ...prev, [idx]: value }))
        extend()
      },
      [extend],
    )

    const toggleMulti = useCallback(
      (idx: number, label: string) => {
        setAnswers((prev) => {
          const current = (prev[idx] as Set<string>) ?? new Set<string>()
          return { ...prev, [idx]: toggleSetItem(current, label) }
        })
        extend()
      },
      [extend],
    )

    // ── 완료 상태 ──
    if (status.type === 'complete' || result !== undefined || submitState === 'submitted') {
      return <CompletedBadge result={submittedDisplay ?? result ?? answers[0]} />
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

    // ── requires-action: 입력 UI ──
    const allAnswered = questions.every((_, i) => {
      const val = answers[i]
      if (val instanceof Set) return val.size > 0
      return val !== undefined && val !== ''
    })

    return (
      <div className="moldy-chat-card w-full p-4">
        {/* Header */}
        <div className="mb-3 flex items-center gap-2">
          <MessageSquareQuoteIcon className="size-4 moldy-builder-color-primary-bg-strong" />
          <span className="text-sm font-medium">{t('inputRequired')}</span>
          <CountdownBadge
            formatted={formatted}
            isUrgent={isUrgent}
            expired={remaining <= 0}
            label={t('expiresIn')}
            expiredLabel={t('expired')}
            className="ml-auto"
          />
        </div>

        {/* Questions */}
        {args?.mode === 'question_flow' ? (
          <QuestionFlowCard
            id={approvalId}
            title={args.title}
            questions={questions}
            submitting={submitState === 'submitting'}
            onInteract={extend}
            onSubmit={(response) => submitResponse(response.message, response.displayText)}
          />
        ) : args?.mode === 'option_list' ? (
          <OptionListCard
            id={approvalId}
            title={args.title}
            options={optionListOptions}
            minSelections={args.minSelections}
            maxSelections={args.maxSelections}
            submitting={submitState === 'submitting'}
            onInteract={extend}
            onSubmit={(response) => submitResponse(response.message, response.displayText)}
          />
        ) : (
          <>
            <div className="space-y-4">
              {questions.map((q, i) => {
                switch (q.type) {
                  case 'single_select':
                    return (
                      <SingleSelectInput
                        key={i}
                        question={q}
                        selected={(answers[i] as string) ?? null}
                        onSelect={(v) => {
                          updateAnswer(i, v)
                          // 질문 1개 + single_select → 즉시 제출
                          if (questions.length === 1) {
                            void submitResponse(v, v)
                          }
                        }}
                      />
                    )
                  case 'multi_select':
                    return (
                      <MultiSelectInput
                        key={i}
                        question={q}
                        selected={(answers[i] as Set<string>) ?? new Set<string>()}
                        onToggle={(v) => toggleMulti(i, v)}
                      />
                    )
                  case 'text':
                  default:
                    return (
                      <TextInput
                        key={i}
                        question={q}
                        value={(answers[i] as string) ?? ''}
                        onChange={(v) => updateAnswer(i, v)}
                        onFocus={extend}
                        placeholder={t('placeholder')}
                      />
                    )
                }
              })}
            </div>

            {/* Submit */}
            <div className="mt-4 flex justify-end">
              <button
                type="button"
                onClick={() => handleSubmit()}
                disabled={!allAnswered || submitState === 'submitting'}
                className={cn(
                  'flex items-center gap-1.5 rounded-full px-4 py-2 text-xs font-medium transition-[background-color,color,opacity]',
                  allAnswered && submitState === 'idle'
                    ? 'bg-primary text-primary-foreground hover:bg-primary/90'
                    : 'cursor-not-allowed bg-muted text-muted-foreground',
                )}
              >
                {submitState === 'submitting' ? (
                  <>
                    <Loader2Icon className="size-3 animate-spin" />
                    {t('sending')}
                  </>
                ) : (
                  <>
                    <SendIcon className="size-3" />
                    {t('confirm')}
                  </>
                )}
              </button>
            </div>
          </>
        )}
      </div>
    )
  },
})
