'use client'

import Link from 'next/link'
import { useRouter } from 'next/navigation'
import { useTranslations } from 'next-intl'
import { WrenchIcon, StarIcon, Settings2Icon, CpuIcon } from 'lucide-react'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { useToggleFavorite } from '@/lib/hooks/use-agents'
import { AgentAvatar } from '@/components/agent/agent-avatar'
import { cn } from '@/lib/utils'
import { formatRelativeKo } from '@/lib/utils/format-relative-time'
import type { Agent, AgentSummary } from '@/lib/types'

interface AgentCardProps {
  agent: Agent | AgentSummary
}

const STATUS_DOT = {
  active: 'moldy-status-success moldy-status-dot',
  error: 'moldy-status-danger moldy-status-dot',
  inactive: 'moldy-status-warn moldy-status-dot',
} as const

export function AgentCard({ agent }: AgentCardProps) {
  const router = useRouter()
  const { mutate: toggleFavorite } = useToggleFavorite()
  const t = useTranslations('agent.card')

  const dot =
    agent.status === 'active'
      ? STATUS_DOT.active
      : agent.status === 'error'
        ? STATUS_DOT.error
        : STATUS_DOT.inactive

  const statusLabel =
    agent.status === 'active'
      ? t('status.active')
      : agent.status === 'error'
        ? t('status.error')
        : t('status.inactive')

  const stopProp = (e: React.MouseEvent) => {
    e.preventDefault()
    e.stopPropagation()
  }

  const modelName =
    'model_display_name' in agent ? agent.model_display_name : agent.model?.display_name
  const fallbackCount =
    'fallback_count' in agent ? agent.fallback_count : (agent.model_fallback_ids?.length ?? 0)
  const toolCount = 'tool_count' in agent ? agent.tool_count : agent.tools.length

  return (
    <Link href={`/agents/${agent.id}`} className="moldy-card-link group">
      <Card className={cn('moldy-card-hover h-full gap-3 py-5')}>
        <CardHeader className="gap-2 px-5">
          <div className="flex items-start gap-3">
            <AgentAvatar imageUrl={agent.image_url} name={agent.name} size="md" />
            <div className="min-w-0 flex-1">
              <CardTitle className="truncate moldy-ui-card-title font-semibold tracking-tight transition-colors group-hover:text-primary-strong">
                {agent.name}
              </CardTitle>
              <div className="mt-1 flex items-center gap-1.5 text-xs text-muted-foreground">
                <span className={cn('inline-block size-1.5 rounded-full ring-2', dot)} />
                {statusLabel}
              </div>
            </div>
            <button
              type="button"
              onClick={(e) => {
                stopProp(e)
                toggleFavorite(agent.id)
              }}
              className="-mr-1 rounded-md p-1 transition-colors hover:bg-accent"
              aria-label={agent.is_favorite ? t('favoriteRemove') : t('favoriteAdd')}
            >
              <StarIcon
                className={cn(
                  'size-4 transition-colors',
                  agent.is_favorite
                    ? 'moldy-status-warn moldy-status-fill text-status-warn'
                    : 'text-muted-foreground hover:text-status-warn',
                )}
              />
            </button>
          </div>
          <CardDescription className="line-clamp-2 min-h-[2.5rem] moldy-ui-body-sm leading-relaxed">
            {agent.description || t('noDescription')}
          </CardDescription>
        </CardHeader>

        <CardContent className="px-5">
          <div className="flex flex-wrap items-center gap-1.5 text-xs">
            <span className="inline-flex items-center gap-1 rounded-md bg-muted px-2 py-0.5 font-medium">
              <CpuIcon className="size-3" />
              {modelName ?? (
                <span className="moldy-status-warn moldy-status-text">{t('noModel')}</span>
              )}
            </span>
            {fallbackCount > 0 && (
              <span
                className="moldy-status-surface moldy-status-warn inline-flex items-center gap-1 rounded-md px-2 py-0.5 font-medium"
                title={t('fallbackTitle', { count: fallbackCount })}
                data-testid="agent-card-fallback-badge"
              >
                +{fallbackCount} fallback
              </span>
            )}
            {toolCount > 0 && (
              <span className="inline-flex items-center gap-1 rounded-md bg-muted px-2 py-0.5 font-medium">
                <WrenchIcon className="size-3" />
                {toolCount}
              </span>
            )}
          </div>
        </CardContent>

        <div className="mx-5 mt-1 flex items-center justify-between border-t border-dashed border-border pt-3">
          <span className="text-xs text-muted-foreground">
            {agent.last_used_at
              ? t('lastUsed', { time: formatRelativeKo(agent.last_used_at) })
              : t('neverUsed')}
          </span>
          <div className="flex items-center gap-1 opacity-0 transition-opacity duration-200 group-hover:opacity-100 group-focus-within:opacity-100">
            <button
              type="button"
              onClick={(e) => {
                stopProp(e)
                router.push(`/agents/${agent.id}/settings`)
              }}
              className="rounded-md p-1 transition-colors hover:bg-accent"
              aria-label={t('settings')}
            >
              <Settings2Icon className="size-4 text-muted-foreground transition-colors hover:text-foreground" />
            </button>
          </div>
        </div>
      </Card>
    </Link>
  )
}
