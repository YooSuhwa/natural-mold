'use client'

import type { ReactNode } from 'react'
import {
  ThreadPrimitive,
  MessagePrimitive,
  ComposerPrimitive,
} from '@assistant-ui/react'
import { SendIcon, SparklesIcon } from 'lucide-react'
import { useTranslations } from 'next-intl'
import { StreamdownTextPrimitive } from '@assistant-ui/react-streamdown'
import { Button } from '@/components/ui/button'
import { cn } from '@/lib/utils'

interface BuilderThreadProps {
  buildStatus: 'idle' | 'building' | 'preview' | 'failed'
  /** Build 결과 카드 (PhaseTimeline, IntentCard, etc.) — idle 상태에서는 null */
  buildResultCards: ReactNode
}

/**
 * Builder 전용 Thread 컴포넌트.
 *
 * 일반 채팅 Thread와 다르게:
 * - idle 상태: 초기 질문 + Composer (큰 textarea)
 * - building/preview: 사용자 요청(UserMessage) + 빌드 결과(AssistantMessage 영역) + 기존 _components
 * - Composer는 idle 상태에서만 활성화 (빌드 시작 후 숨김)
 */
export function BuilderThread({ buildStatus, buildResultCards }: BuilderThreadProps) {
  const t = useTranslations('agent.creation')

  return (
    <ThreadPrimitive.Root className="flex flex-1 flex-col overflow-hidden">
      <ThreadPrimitive.Viewport className="flex-1 overflow-y-auto">
        <div className="mx-auto max-w-2xl space-y-6 p-6">
          {/* Idle: Initial question prompt */}
          <ThreadPrimitive.Empty>
            <div className="rounded-xl border bg-background p-5">
              <div className="flex items-start gap-2.5">
                <SparklesIcon className="mt-0.5 size-5 shrink-0 text-primary" />
                <p className="text-base font-semibold leading-relaxed">{t('initialQuestion')}</p>
              </div>
            </div>
          </ThreadPrimitive.Empty>

          {/* Messages (user request + assistant progress summary) */}
          <ThreadPrimitive.Messages
            components={{
              UserMessage: BuilderUserMessage,
              AssistantMessage: BuilderAssistantMessage,
            }}
          />

          {/* Build result cards — rendered outside Message primitives for full control */}
          {buildResultCards}
        </div>
      </ThreadPrimitive.Viewport>

      {/* Composer — idle 상태에서 입력, 빌드 중에는 비활성화 */}
      {buildStatus === 'idle' && (
        <div className="mx-auto w-full max-w-2xl px-6 pb-6">
          <BuilderComposer />
        </div>
      )}
    </ThreadPrimitive.Root>
  )
}

/** 사용자 요청 — 초록 계열 버블 */
function BuilderUserMessage() {
  const t = useTranslations('agent.creation')

  return (
    <div className="rounded-xl border bg-primary/5 px-4 py-3">
      <div className="text-sm">
        <span className="font-medium text-primary">{t('userRequestLabel')}</span>{' '}
        <MessagePrimitive.Content />
      </div>
    </div>
  )
}

/** 어시스턴트 요약 — Phase 진행 텍스트 (기존 _components가 별도로 렌더링되므로 간략하게) */
function BuilderAssistantMessage() {
  return (
    <div className="text-sm leading-relaxed text-muted-foreground">
      <MessagePrimitive.Content
        components={{
          Text: () => (
            <div className="py-1">
              <StreamdownTextPrimitive />
            </div>
          ),
        }}
      />
    </div>
  )
}

/** Builder 전용 Composer — 큰 textarea + 전송 버튼 */
function BuilderComposer() {
  const t = useTranslations('agent.creation')

  return (
    <ComposerPrimitive.Root className="space-y-4">
      <ComposerPrimitive.Input
        placeholder={t('initialPlaceholder')}
        submitMode="enter"
        rows={3}
        className={cn(
          'min-h-[80px] max-h-[160px] w-full resize-none rounded-xl border border-input bg-transparent px-3.5 py-3 text-sm leading-relaxed outline-none transition-colors',
          'placeholder:text-muted-foreground',
          'focus-visible:border-ring focus-visible:ring-3 focus-visible:ring-ring/50',
        )}
      />
      <div className="flex justify-end">
        <ComposerPrimitive.Send asChild>
          <Button size="lg">
            <SendIcon className="mr-1.5 size-4" />
            {t('startButton')}
          </Button>
        </ComposerPrimitive.Send>
      </div>
    </ComposerPrimitive.Root>
  )
}
