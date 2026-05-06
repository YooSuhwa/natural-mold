'use client'

import { useState, useCallback, useMemo, useRef } from 'react'
import { makeAssistantToolUI } from '@assistant-ui/react'
import { MessageSquareQuoteIcon, CheckCircle2Icon, SendIcon, Loader2Icon } from 'lucide-react'
import { useTranslations } from 'next-intl'
import { cn, toggleSetItem } from '@/lib/utils'
import { useHiTL } from '@/lib/chat/hitl-context'
import { useApprovalDeadline } from '@/lib/hooks/use-approval-deadline'
import type { UserInputQuestion } from '@/lib/types'
import { CountdownBadge } from './countdown-badge'

interface AskUserArgs {
  /** 복수 질문 */
  questions?: UserInputQuestion[]
  /** 단일 질문 폴백 */
  question?: string
  type?: UserInputQuestion['type']
  options?: UserInputQuestion['options']
  /** 입력 만료 timeout (초) — 미지정 시 5분 */
  timeout_seconds?: number
  /** 입력 식별자 — deadline 리셋 키로 사용 */
  approval_id?: string
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
              'rounded-full border px-3 py-1.5 text-xs transition-all',
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
                'flex cursor-pointer items-center gap-2.5 rounded-lg border px-3 py-2 text-xs transition-all',
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
        className="w-full resize-none rounded-lg border border-border bg-background px-3 py-2 text-sm outline-none transition-colors placeholder:text-muted-foreground focus:border-primary/50 focus:ring-1 focus:ring-primary/30"
        rows={3}
      />
    </div>
  )
}

function CompletedBadge({ result }: { result: unknown }) {
  const t = useTranslations('chat.userInput')
  const display = typeof result === 'string' ? result : JSON.stringify(result)
  return (
    <div className="flex items-center gap-2 rounded-xl border border-emerald-200 bg-emerald-50 px-3 py-2 text-xs dark:border-emerald-900 dark:bg-emerald-950">
      <CheckCircle2Icon className="size-3.5 shrink-0 text-emerald-500" />
      <span className="font-medium text-emerald-700 dark:text-emerald-300">{t('completed')}</span>
      {display && (
        <span className="truncate text-emerald-600/80 dark:text-emerald-400/80">{display}</span>
      )}
    </div>
  )
}

/** string[] → {label}[] 변환 */
function normalizeOptions(
  options?: (string | { label: string; description?: string })[],
): UserInputQuestion['options'] {
  if (!options || options.length === 0) return undefined
  return options.map((o) => (typeof o === 'string' ? { label: o } : o))
}

/** args에서 질문 배열 정규화 */
function normalizeQuestions(args: AskUserArgs): UserInputQuestion[] {
  if (args.questions && args.questions.length > 0) {
    return args.questions.map((q) => ({
      ...q,
      options: normalizeOptions(q.options as (string | { label: string })[]),
      type: q.type ?? (q.options?.length ? 'single_select' : 'text'),
    }))
  }
  if (args.question) {
    const opts = normalizeOptions(args.options as (string | { label: string })[] | undefined)
    return [
      {
        question: args.question,
        // options가 있으면 자동으로 single_select
        type: args.type ?? (opts?.length ? 'single_select' : 'text'),
        options: opts,
      },
    ]
  }
  return []
}

/**
 * UserInputUI — Builder v3 전용 사용자 입력 카드.
 *
 * builder_v3가 phase2_intent / router fallback에서 발행하는 native interrupt
 * (`{type:'ask_user', question, options}`)는 backend streaming.py 어댑터가
 * 표준 `respond` action chunk로 변환하고, 이 컴포넌트가 toolName: 'ask_user'
 * 로 매칭되어 렌더된다.
 *
 * @see backend/app/agent_runtime/streaming.py:_interrupt_to_standard_chunk
 * @see backend/app/agent_runtime/builder_v3/nodes/{phase2_intent,router}.py
 */
export const UserInputUI = makeAssistantToolUI<AskUserArgs, unknown>({
  toolName: 'ask_user',
  render: function AskUserRender({ args, result, status }) {
    const t = useTranslations('chat.userInput')
    const hitl = useHiTL()
    const [answers, setAnswers] = useState<Answers>({})
    const [submitState, setSubmitState] = useState<'idle' | 'submitting' | 'submitted'>('idle')

    const questions = useMemo(() => normalizeQuestions(args ?? {}), [args])

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
          const val = answers[i]
          if (val instanceof Set) {
            response[q.question] = Array.from(val)
          } else {
            response[q.question] = val ?? ''
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
        await hitl?.onResumeDecisions([{ type: 'respond', message }], displayText)
        setSubmitState('submitted')
      },
      [answers, questions, hitl],
    )

    // 만료 시 빈 답변으로 자동 제출 — 에이전트 graph가 무한히 paused되지 않도록
    const expireMessage = t('autoSkipped')
    const handleExpire = useCallback(() => {
      if (submitState !== 'idle') return
      void handleSubmit({ skipReason: expireMessage })
    }, [handleSubmit, submitState, expireMessage])

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
      return <CompletedBadge result={result ?? answers[0]} />
    }

    // ── 로딩 상태 ──
    if (status.type === 'running') {
      return (
        <div className="flex items-center gap-2 rounded-xl border bg-muted/20 px-3 py-2 text-xs">
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
      <div className="w-full rounded-xl border bg-background p-4 shadow-sm">
        {/* Header */}
        <div className="mb-3 flex items-center gap-2">
          <MessageSquareQuoteIcon className="size-4 text-primary-strong" />
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
                        setSubmitState('submitted')
                        hitl?.onResumeDecisions([{ type: 'respond', message: v }], v)
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
              'flex items-center gap-1.5 rounded-full px-4 py-2 text-xs font-medium transition-all',
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
      </div>
    )
  },
})
