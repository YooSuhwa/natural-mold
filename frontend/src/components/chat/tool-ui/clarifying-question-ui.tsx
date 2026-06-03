'use client'

import { useCallback, useMemo, useRef, useState } from 'react'
import { makeAssistantToolUI, useAui } from '@assistant-ui/react'
import { MessageCircleQuestionIcon } from 'lucide-react'
import { useTranslations } from 'next-intl'
import { cn } from '@/lib/utils'
import { useApprovalDeadline } from '@/lib/hooks/use-approval-deadline'
import { CountdownBadge } from './countdown-badge'

const STATE_CLASS = {
  idle: 'cursor-pointer border-border hover:border-primary/50 hover:bg-accent',
  selected: 'border-primary bg-primary/10 text-primary-strong ring-1 ring-primary/30',
  dimmed: 'cursor-default opacity-40',
} as const

interface ClarifyingArgs {
  question?: string
  option_1?: string
  option_2?: string
  option_3?: string
  /** 선택 만료 timeout (초) — 미지정 시 5분 */
  timeout_seconds?: number
  /** 식별자 — deadline 리셋 키로 사용 */
  approval_id?: string
}

interface ClarifyingResult {
  type: 'clarifying_question'
  question: string
  options: string[]
}

/**
 * Fix 에이전트 `ask_clarifying_question` 도구 UI.
 * Backend는 일반 LLM tool로 옵션 3개 + "직접 입력"을 반환.
 * 사용자가 옵션 클릭 시 setText + send로 새 사용자 메시지를 보낸다.
 *
 * HITL이 아니라 backend pause가 없으므로 만료 시 별도 액션 없이
 * 옵션 버튼만 disabled 처리하여 시각적 urgency만 표현.
 */
export const ClarifyingQuestionUI = makeAssistantToolUI<ClarifyingArgs, string>({
  toolName: 'ask_clarifying_question',
  render: function ClarifyingRender({ args, result, status }) {
    const t = useTranslations('chat.clarifying')
    const aui = useAui()
    const [picked, setPicked] = useState<string | null>(null)
    const directInputLabel = t('directInput')

    const parsed = useMemo<ClarifyingResult | null>(() => {
      if (typeof result === 'string') {
        try {
          return JSON.parse(result) as ClarifyingResult
        } catch {
          return null
        }
      }
      return null
    }, [result])

    const question = parsed?.question ?? args?.question ?? ''
    const options =
      parsed?.options ??
      ([args?.option_1, args?.option_2, args?.option_3, directInputLabel].filter(
        Boolean,
      ) as string[])

    // 카드 인스턴스별 안정 키 — args.approval_id 우선, 없으면 마운트 시 생성
    const fallbackIdRef = useRef<string>(`clarifying-${Math.random().toString(36).slice(2)}`)
    const approvalId = args?.approval_id ?? fallbackIdRef.current

    // 만료는 시각적 신호만 — backend가 paused 상태가 아니므로 별도 resume 불필요
    const handleExpire = useCallback(() => {
      // no-op: remaining<=0이 picked===null과 함께 disabled 트리거
    }, [])

    const { remaining, isUrgent, formatted, extend } = useApprovalDeadline({
      approvalId,
      initialTimeoutSeconds: args?.timeout_seconds,
      onExpire: handleExpire,
      active: picked === null,
    })

    if (status.type === 'running' && !args?.question) {
      // tool 호출 중 args 아직 없음 — 빈 상태
      return null
    }

    const expired = remaining <= 0
    const disabled = picked !== null || expired

    const handleClick = (opt: string) => {
      if (disabled) return
      extend()
      setPicked(opt)
      if (opt === directInputLabel) {
        // 사용자가 직접 입력 — disabled 처리만, 입력창에 직접 타이핑
        return
      }
      try {
        // SuggestionTrigger와 동일한 패턴 — thread에 직접 user message append
        aui.thread().append({
          content: [{ type: 'text', text: opt }],
        })
      } catch (err) {
        console.warn('[clarifying] thread append error:', err)
        setPicked(null) // 실패 시 다시 클릭 가능하게
      }
    }

    return (
      <div className="moldy-chat-card mt-2 p-4">
        <div className="mb-3 flex items-start gap-2">
          <MessageCircleQuestionIcon className="mt-0.5 size-4 shrink-0 text-primary-strong" />
          <p className="flex-1 text-sm font-medium">{question}</p>
          <CountdownBadge
            formatted={formatted}
            isUrgent={isUrgent}
            expired={expired}
            label={t('expiresIn')}
            expiredLabel={t('expired')}
          />
        </div>
        <div className="flex flex-wrap gap-2">
          {options.map((opt) => {
            const state: 'idle' | 'selected' | 'dimmed' = disabled
              ? picked === opt
                ? 'selected'
                : 'dimmed'
              : 'idle'
            return (
              <button
                key={opt}
                type="button"
                disabled={disabled}
                onClick={() => handleClick(opt)}
                className={cn(
                  'rounded-full border px-3 py-1.5 text-xs transition-[background-color,border-color,color,box-shadow]',
                  STATE_CLASS[state],
                )}
              >
                {opt}
              </button>
            )
          })}
        </div>
      </div>
    )
  },
})
