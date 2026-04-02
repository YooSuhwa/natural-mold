'use client'

import Link from 'next/link'
import { PlusIcon, SparklesIcon, MessageSquareIcon, LayoutTemplateIcon } from 'lucide-react'
import { useAgents } from '@/lib/hooks/use-agents'
import { useUsageSummary } from '@/lib/hooks/use-usage'
import { Button } from '@/components/ui/button'
import { Card, CardContent } from '@/components/ui/card'
import { AgentCard } from '@/components/agent/agent-card'
import { AgentCardSkeleton } from '@/components/agent/agent-card-skeleton'
import { EmptyState } from '@/components/shared/empty-state'

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

export default function DashboardPage() {
  const { data: agents, isLoading: agentsLoading } = useAgents()
  const { data: usage } = useUsageSummary()

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
          <h2 className="mb-4 text-lg font-semibold tracking-tight">내 에이전트</h2>
          <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
            {agents.map((agent) => (
              <AgentCard key={agent.id} agent={agent} />
            ))}
          </div>
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
