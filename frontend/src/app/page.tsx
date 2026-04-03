'use client'

import { useMemo, useState } from 'react'
import Link from 'next/link'
import {
  PlusIcon,
  SparklesIcon,
  MessageSquareIcon,
  LayoutTemplateIcon,
  SearchIcon,
  StarIcon,
  ArrowUpDownIcon,
} from 'lucide-react'
import { useAgents } from '@/lib/hooks/use-agents'
import { useUsageSummary } from '@/lib/hooks/use-usage'
import { Button } from '@/components/ui/button'
import { Card, CardContent } from '@/components/ui/card'
import { Input } from '@/components/ui/input'
import {
  DropdownMenu,
  DropdownMenuTrigger,
  DropdownMenuContent,
  DropdownMenuItem,
} from '@/components/ui/dropdown-menu'
import { AgentCard } from '@/components/agent/agent-card'
import { AgentCardSkeleton } from '@/components/agent/agent-card-skeleton'
import { EmptyState } from '@/components/shared/empty-state'
import type { Agent } from '@/lib/types'

const quickActions = [
  {
    label: '대화로 만들기',
    description: 'AI와 대화하며 에이전트를 구성합니다',
    href: '/agents/new/conversational',
    icon: MessageSquareIcon,
  },
  {
    label: '템플릿으로 만들기',
    description: '준비된 템플릿에서 골라 바로 시작합니다',
    href: '/agents/new/template',
    icon: LayoutTemplateIcon,
  },
]

type SortKey = 'latest' | 'name' | 'favorite'

const SORT_LABELS: Record<SortKey, string> = {
  latest: '최신순',
  name: '이름순',
  favorite: '즐겨찾기',
}

export default function DashboardPage() {
  const { data: agents, isLoading: agentsLoading } = useAgents()
  const { data: usage } = useUsageSummary()
  const [search, setSearch] = useState('')
  const [sortBy, setSortBy] = useState<SortKey>('latest')
  const [showFavoritesOnly, setShowFavoritesOnly] = useState(false)

  const filteredAgents = useMemo(() => {
    if (!agents) return []
    let result: Agent[] = agents

    // Search filter
    if (search.trim()) {
      const q = search.trim().toLowerCase()
      result = result.filter(
        (a) =>
          a.name.toLowerCase().includes(q) ||
          (a.description && a.description.toLowerCase().includes(q))
      )
    }

    // Favorites filter
    if (showFavoritesOnly) {
      result = result.filter((a) => a.is_favorite)
    }

    // Sort
    result = [...result].sort((a, b) => {
      if (sortBy === 'name') return a.name.localeCompare(b.name, 'ko')
      if (sortBy === 'favorite') {
        if (a.is_favorite !== b.is_favorite) return a.is_favorite ? -1 : 1
      }
      return new Date(b.updated_at).getTime() - new Date(a.updated_at).getTime()
    })

    return result
  }, [agents, search, sortBy, showFavoritesOnly])

  return (
    <div className="flex flex-1 flex-col gap-6 overflow-auto p-6">
      {/* Hero Section */}
      <div className="flex items-start justify-between rounded-xl bg-muted/30 p-6">
        <div className="space-y-1">
          <h1 className="text-2xl font-bold tracking-tight">안녕하세요!</h1>
          <p className="text-sm text-muted-foreground">
            AI 에이전트를 만들어 반복 업무를 자동화하세요.
          </p>
        </div>
        <Link href="/agents/new">
          <Button>
            <PlusIcon className="size-4" data-icon="inline-start" />새 에이전트
          </Button>
        </Link>
      </div>

      {/* Quick Action Cards */}
      <div className="grid gap-4 sm:grid-cols-2">
        {quickActions.map((action) => (
          <Link key={action.href} href={action.href} className="group">
            <Card className="cursor-pointer transition-colors hover:border-primary/40">
              <CardContent className="flex items-center gap-4 p-4">
                <div className="flex size-10 shrink-0 items-center justify-center rounded-lg bg-primary/10">
                  <action.icon className="size-5 text-primary" />
                </div>
                <div>
                  <p className="text-sm font-medium group-hover:text-primary transition-colors">
                    {action.label}
                  </p>
                  <p className="text-xs text-muted-foreground">{action.description}</p>
                </div>
              </CardContent>
            </Card>
          </Link>
        ))}
      </div>

      {/* Agent Grid */}
      {agentsLoading ? (
        <div>
          <h2 className="mb-4 text-lg font-semibold tracking-tight">내 에이전트</h2>
          <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
            {Array.from({ length: 3 }).map((_, i) => (
              <AgentCardSkeleton key={i} />
            ))}
          </div>
        </div>
      ) : agents && agents.length > 0 ? (
        <div>
          {/* Search / Sort / Filter Bar */}
          <div className="mb-4 flex flex-wrap items-center gap-3">
            <h2 className="text-lg font-semibold tracking-tight">내 에이전트</h2>
            <div className="ml-auto flex items-center gap-2">
              <div className="relative">
                <SearchIcon className="absolute left-2.5 top-1/2 size-4 -translate-y-1/2 text-muted-foreground" />
                <Input
                  placeholder="에이전트 검색..."
                  value={search}
                  onChange={(e) => setSearch(e.target.value)}
                  className="h-9 w-48 pl-8 text-sm"
                />
              </div>

              <Button
                variant={showFavoritesOnly ? 'default' : 'outline'}
                size="sm"
                onClick={() => setShowFavoritesOnly(!showFavoritesOnly)}
                className="h-9"
              >
                <StarIcon className={`size-4 ${showFavoritesOnly ? 'fill-current' : ''}`} />
              </Button>

              <DropdownMenu>
                <DropdownMenuTrigger
                  render={
                    <Button variant="outline" size="sm" className="h-9 gap-1.5" />
                  }
                >
                  <ArrowUpDownIcon className="size-3.5" />
                  <span className="text-xs">{SORT_LABELS[sortBy]}</span>
                </DropdownMenuTrigger>
                <DropdownMenuContent align="end">
                  <DropdownMenuItem onClick={() => setSortBy('latest')}>
                    최신순
                    {sortBy === 'latest' && <span className="ml-auto text-xs">✓</span>}
                  </DropdownMenuItem>
                  <DropdownMenuItem onClick={() => setSortBy('name')}>
                    이름순
                    {sortBy === 'name' && <span className="ml-auto text-xs">✓</span>}
                  </DropdownMenuItem>
                  <DropdownMenuItem onClick={() => setSortBy('favorite')}>
                    즐겨찾기 우선
                    {sortBy === 'favorite' && <span className="ml-auto text-xs">✓</span>}
                  </DropdownMenuItem>
                </DropdownMenuContent>
              </DropdownMenu>
            </div>
          </div>

          {filteredAgents.length > 0 ? (
            <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
              {filteredAgents.map((agent) => (
                <AgentCard key={agent.id} agent={agent} />
              ))}
            </div>
          ) : (
            <div className="flex flex-col items-center justify-center rounded-xl border border-dashed p-8 text-center">
              <SearchIcon className="mb-2 size-6 text-muted-foreground" />
              <p className="text-sm text-muted-foreground">
                검색 결과가 없습니다.
              </p>
            </div>
          )}
        </div>
      ) : (
        <EmptyState
          icon={<SparklesIcon className="size-6" />}
          title="첫 에이전트를 만들어보세요"
          description="위 카드에서 원하는 방식을 선택하세요."
        />
      )}

      {/* Usage Summary */}
      {usage && usage.total_tokens > 0 && (
        <div className="rounded-xl border bg-muted/30 p-4">
          <h2 className="mb-2 text-sm font-medium text-muted-foreground">이번 달 사용량</h2>
          <div className="flex items-center gap-6 text-sm">
            <div>
              <span className="text-muted-foreground">총 토큰: </span>
              <span className="font-medium">{usage.total_tokens.toLocaleString()}</span>
            </div>
            <div>
              <span className="text-muted-foreground">추정 비용: </span>
              <span className="font-medium">${usage.estimated_cost_usd.toFixed(2)}</span>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}
