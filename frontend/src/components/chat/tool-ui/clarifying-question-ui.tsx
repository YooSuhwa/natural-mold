'use client'

import { useMemo, useState } from 'react'
import { makeAssistantToolUI, useAui } from '@assistant-ui/react'
import { MessageCircleQuestionIcon } from 'lucide-react'
import { useTranslations } from 'next-intl'
import { cn } from '@/lib/utils'

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

    if (status.type === 'running' && !args?.question) {
      // tool 호출 중 args 아직 없음 — 빈 상태
      return null
    }

    const handleClick = (opt: string) => {
      if (picked) return
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
      <div className="mt-2 rounded-xl border bg-background p-4 shadow-sm">
        <div className="mb-3 flex items-start gap-2">
          <MessageCircleQuestionIcon className="mt-0.5 size-4 shrink-0 text-primary-strong" />
          <p className="text-sm font-medium">{question}</p>
        </div>
        <div className="flex flex-wrap gap-2">
          {options.map((opt) => {
            const state: 'idle' | 'selected' | 'dimmed' =
              picked === null ? 'idle' : picked === opt ? 'selected' : 'dimmed'
            return (
              <button
                key={opt}
                type="button"
                disabled={picked !== null}
                onClick={() => handleClick(opt)}
                className={cn(
                  'rounded-full border px-3 py-1.5 text-xs transition-all',
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
