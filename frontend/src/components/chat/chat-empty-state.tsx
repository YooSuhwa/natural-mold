'use client'

import { useComposerRuntime } from '@assistant-ui/react'
import { SparklesIcon } from 'lucide-react'
import { useTranslations } from 'next-intl'
import { AgentAvatar } from '@/components/agent/agent-avatar'
import type { Agent } from '@/lib/types'

interface ChatEmptyStateProps {
  readonly agent: Agent | undefined
  readonly fallback: string
}

export function ChatEmptyState({ agent, fallback }: ChatEmptyStateProps) {
  const t = useTranslations('chat')
  const composer = useComposerRuntime({ optional: true })
  const openerQuestions = agent?.opener_questions ?? []

  return (
    <div className="flex flex-col items-center justify-center py-20 text-center">
      <div className="mb-4">
        <AgentAvatar
          imageUrl={agent?.image_url ?? null}
          name={agent?.name ?? t('defaultAgentName')}
          size="lg"
        />
      </div>
      <h2 className="mb-1 text-lg font-semibold">{agent?.name ?? fallback}</h2>
      {agent?.description && (
        <p className="mb-4 max-w-md text-sm text-muted-foreground">{agent.description}</p>
      )}
      <div className="flex items-center gap-1.5 rounded-full bg-primary px-3 py-1 text-xs font-medium text-primary-foreground ring-1 ring-primary-strong/15">
        <SparklesIcon className="size-3.5" />
        <span>{fallback}</span>
      </div>
      {openerQuestions.length > 0 && (
        <div className="mt-6 flex max-w-2xl flex-wrap justify-center gap-2">
          {openerQuestions.map((question) => (
            <button
              key={question}
              type="button"
              onClick={() => composer?.setText(question)}
              className="rounded-full border border-primary-strong/20 bg-background/80 px-3 py-1.5 text-xs transition-colors hover:bg-primary hover:text-primary-foreground"
            >
              {question}
            </button>
          ))}
        </div>
      )}
    </div>
  )
}
