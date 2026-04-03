'use client'

import Link from 'next/link'
import { WrenchIcon, StarIcon } from 'lucide-react'
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
  const { mutate: toggleFavorite } = useToggleFavorite()

  const statusColor =
    agent.status === 'active'
      ? 'bg-emerald-500'
      : agent.status === 'error'
        ? 'bg-red-500'
        : 'bg-yellow-500'

  const statusLabel =
    agent.status === 'active' ? '활성' : agent.status === 'error' ? '오류' : '비활성'

  return (
    <Link
      href={`/agents/${agent.id}`}
      className="group cursor-pointer focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 rounded-xl"
    >
      <Card className="h-full transition-colors hover:border-primary/40">
        <CardHeader>
          <div className="flex items-start justify-between">
            <CardTitle className="group-hover:text-primary transition-colors">
              {agent.name}
            </CardTitle>
            <div className="flex items-center gap-1.5">
              <button
                type="button"
                onClick={(e) => {
                  e.preventDefault()
                  e.stopPropagation()
                  toggleFavorite(agent.id)
                }}
                className="rounded-md p-1 hover:bg-accent transition-colors"
                aria-label={agent.is_favorite ? '즐겨찾기 해제' : '즐겨찾기'}
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
          {agent.description && (
            <CardDescription className="line-clamp-2">{agent.description}</CardDescription>
          )}
        </CardHeader>
        <CardContent>
          <div className="flex flex-col gap-2 text-sm text-muted-foreground">
            <div className="flex items-center gap-1.5">
              <span className="font-medium text-foreground">모델:</span>
              <span>{agent.model.display_name}</span>
            </div>
            {agent.tools.length > 0 && (
              <div className="flex items-center gap-1.5">
                <WrenchIcon className="size-3.5" />
                <span>{agent.tools.map((t) => t.name).join(', ')}</span>
              </div>
            )}
          </div>
        </CardContent>
        <CardFooter className="text-xs text-muted-foreground">
          {new Date(agent.created_at).toLocaleDateString('ko-KR')} 생성
        </CardFooter>
      </Card>
    </Link>
  )
}
