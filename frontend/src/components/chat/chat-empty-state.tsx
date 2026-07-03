'use client'

import { useComposerRuntime } from '@assistant-ui/react'
import { BookOpenIcon, PlugIcon, SparklesIcon, WrenchIcon } from 'lucide-react'
import { useTranslations } from 'next-intl'
import { AgentAvatar } from '@/components/agent/agent-avatar'
import { useTemplates } from '@/lib/hooks/use-templates'
import type { Agent } from '@/lib/types'

interface ChatEmptyStateProps {
  readonly agent: Agent | undefined
  readonly fallback: string
}

const MAX_CAPABILITY_CHIPS = 6

interface CapabilityChip {
  readonly kind: 'skill' | 'tool' | 'mcp'
  readonly name: string
}

function capabilityChips(agent: Agent | undefined): CapabilityChip[] {
  if (!agent) return []
  return [
    ...(agent.skills ?? []).map((s) => ({ kind: 'skill' as const, name: s.name })),
    ...(agent.tools ?? []).map((t) => ({ kind: 'tool' as const, name: t.name })),
    ...(agent.mcp_tools ?? []).map((m) => ({ kind: 'mcp' as const, name: m.name })),
  ]
}

function CapabilityIcon({ kind }: { kind: CapabilityChip['kind'] }) {
  const className = 'size-3 shrink-0'
  if (kind === 'skill') return <BookOpenIcon aria-hidden className={className} />
  if (kind === 'mcp') return <PlugIcon aria-hidden className={className} />
  return <WrenchIcon aria-hidden className={className} />
}

export function ChatEmptyState({ agent, fallback }: ChatEmptyStateProps) {
  const t = useTranslations('chat')
  const composer = useComposerRuntime({ optional: true })
  const openerQuestions = agent?.opener_questions ?? []

  // 스타터 폴백: 에이전트에 큐레이션된 opener가 없을 때만 템플릿의
  // usage_example을 프런트 조인으로 가져온다(백엔드 변경 없음, 5분 캐시).
  const needsTemplateStarter = openerQuestions.length === 0 && Boolean(agent?.template_id)
  const { data: templates } = useTemplates(undefined, { enabled: needsTemplateStarter })
  const templateStarter = needsTemplateStarter
    ? (templates?.find((tpl) => tpl.id === agent?.template_id)?.usage_example ?? null)
    : null

  const starters =
    openerQuestions.length > 0 ? openerQuestions : templateStarter ? [templateStarter] : []
  const capabilities = capabilityChips(agent)
  const visibleCapabilities = capabilities.slice(0, MAX_CAPABILITY_CHIPS)
  const extraCapabilities = capabilities.length - visibleCapabilities.length

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
      {capabilities.length > 0 && (
        <div className="mt-5 flex max-w-2xl flex-col items-center gap-2">
          <span className="moldy-ui-caption text-muted-foreground">{t('emptyState.canDo')}</span>
          <div
            className="flex flex-wrap justify-center gap-1.5"
            data-moldy-empty-capabilities="true"
          >
            {visibleCapabilities.map((capability) => (
              <span
                key={`${capability.kind}-${capability.name}`}
                className="inline-flex items-center gap-1 rounded-full border border-border bg-background/80 px-2.5 py-1 text-xs text-muted-foreground"
              >
                <CapabilityIcon kind={capability.kind} />
                <span className="max-w-40 truncate">{capability.name}</span>
              </span>
            ))}
            {extraCapabilities > 0 && (
              <span className="inline-flex items-center rounded-full border border-border bg-background/80 px-2.5 py-1 text-xs text-muted-foreground">
                {t('emptyState.moreCapabilities', { count: extraCapabilities })}
              </span>
            )}
          </div>
        </div>
      )}
      {starters.length > 0 && (
        <div
          className="mt-6 flex max-w-2xl flex-wrap justify-center gap-2"
          data-moldy-empty-starters="true"
        >
          {starters.map((question) => (
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
