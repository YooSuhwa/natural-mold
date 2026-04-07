'use client'

import Link from 'next/link'
import { useRouter } from 'next/navigation'
import { useTranslations, useFormatter } from 'next-intl'
import { WrenchIcon, StarIcon, Settings2Icon, WorkflowIcon, CpuIcon } from 'lucide-react'
import {
  Card,
  CardHeader,
  CardTitle,
  CardDescription,
  CardContent,
  CardFooter,
} from '@/components/ui/card'
import { Badge } from '@/components/ui/badge'
import { useToggleFavorite } from '@/lib/hooks/use-agents'
import type { Agent } from '@/lib/types'

interface AgentCardProps {
  agent: Agent
}

export function AgentCard({ agent }: AgentCardProps) {
  const router = useRouter()
  const { mutate: toggleFavorite } = useToggleFavorite()
  const t = useTranslations('agent.card')
  const format = useFormatter()

  const statusColor =
    agent.status === 'active'
      ? 'bg-emerald-500'
      : agent.status === 'error'
        ? 'bg-red-500'
        : 'bg-yellow-500'

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

  return (
    <Link
      href={`/agents/${agent.id}`}
      className="group cursor-pointer focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 rounded-xl"
    >
      <Card className="h-full transition-colors hover:border-primary/40">
        <CardHeader className="pb-2">
          <div className="flex items-start justify-between">
            <CardTitle className="truncate group-hover:text-primary transition-colors">
              {agent.name}
            </CardTitle>
            <div className="flex items-center gap-1 shrink-0">
              <button
                type="button"
                onClick={(e) => {
                  stopProp(e)
                  toggleFavorite(agent.id)
                }}
                className="rounded-md p-1 hover:bg-accent transition-colors"
                aria-label={agent.is_favorite ? t('favoriteRemove') : t('favoriteAdd')}
              >
                <StarIcon
                  className={`size-4 transition-colors ${
                    agent.is_favorite
                      ? 'fill-yellow-400 text-yellow-400'
                      : 'text-muted-foreground hover:text-yellow-400'
                  }`}
                />
              </button>
              <Badge variant="secondary" className="shrink-0">
                <span className={`mr-1 inline-block size-1.5 rounded-full ${statusColor}`} />
                {statusLabel}
              </Badge>
            </div>
          </div>
          <CardDescription className="line-clamp-2 min-h-[2.5rem]">
            {agent.description || t('noDescription')}
          </CardDescription>
        </CardHeader>

        <CardContent className="pt-0">
          <div className="flex items-center gap-3 text-xs text-muted-foreground">
            <span className="flex items-center gap-1">
              <CpuIcon className="size-3.5" />
              {agent.model.display_name}
            </span>
            {agent.tools.length > 0 && (
              <span className="inline-flex items-center gap-1 rounded-md bg-muted px-1.5 py-0.5 font-medium">
                <WrenchIcon className="size-3" />
                {agent.tools.length}
              </span>
            )}
          </div>
        </CardContent>

        <CardFooter>
          <div className="flex w-full items-center justify-between">
            <span className="text-xs text-muted-foreground">
              {t('createdAt', {
                date: format.dateTime(new Date(agent.created_at), { dateStyle: 'medium' }),
              })}
            </span>
            <div className="flex items-center gap-1 opacity-0 group-hover:opacity-100 focus-within:opacity-100 transition-opacity duration-200">
              <button
                type="button"
                onClick={(e) => {
                  stopProp(e)
                  router.push(`/agents/${agent.id}/visual-settings`)
                }}
                className="rounded-md p-1 hover:bg-accent transition-colors"
                aria-label={t('visualSettings')}
              >
                <WorkflowIcon className="size-4 text-muted-foreground hover:text-foreground transition-colors" />
              </button>
              <button
                type="button"
                onClick={(e) => {
                  stopProp(e)
                  router.push(`/agents/${agent.id}/settings`)
                }}
                className="rounded-md p-1 hover:bg-accent transition-colors"
                aria-label={t('settings')}
              >
                <Settings2Icon className="size-4 text-muted-foreground hover:text-foreground transition-colors" />
              </button>
            </div>
          </div>
        </CardFooter>
      </Card>
    </Link>
  )
}
