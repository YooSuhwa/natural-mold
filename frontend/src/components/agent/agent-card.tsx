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
import type { Agent } from '@/lib/types'

interface AgentCardProps {
  agent: Agent
}

const STATUS_DOT = {
  active: 'bg-emerald-500 ring-emerald-500/20',
  error: 'bg-red-500 ring-red-500/20',
  inactive: 'bg-amber-500 ring-amber-500/20',
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

  const fallbackCount = agent.model_fallback_ids?.length ?? 0

  return (
    <Link
      href={`/agents/${agent.id}`}
      className="group block rounded-xl focus-visible:outline-hidden focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2"
    >
      <Card
        className={cn(
          'h-full gap-3 py-5 transition-[box-shadow,transform] duration-150',
          'hover:-translate-y-px hover:shadow-[0_10px_22px_-12px_rgba(16,185,129,0.22)]',
          'hover:ring-emerald-300/70 dark:hover:ring-emerald-400/30',
        )}
      >
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
                    ? 'fill-amber-400 text-amber-400'
                    : 'text-muted-foreground hover:text-amber-400',
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
              {agent.model?.display_name ?? (
                <span className="text-amber-600 dark:text-amber-300">{t('noModel')}</span>
              )}
            </span>
            {fallbackCount > 0 && (
              <span
                className="inline-flex items-center gap-1 rounded-md border border-amber-200 bg-amber-50 px-2 py-0.5 font-medium text-amber-700 dark:border-amber-500/30 dark:bg-amber-500/15 dark:text-amber-300"
                title={t('fallbackTitle', { count: fallbackCount })}
                data-testid="agent-card-fallback-badge"
              >
                +{fallbackCount} fallback
              </span>
            )}
            {agent.tools.length > 0 && (
              <span className="inline-flex items-center gap-1 rounded-md bg-muted px-2 py-0.5 font-medium">
                <WrenchIcon className="size-3" />
                {agent.tools.length}
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
