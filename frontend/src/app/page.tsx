"use client"

import Link from "next/link"
import { PlusIcon, BotIcon } from "lucide-react"
import { useAgents } from "@/lib/hooks/use-agents"
import { useUsageSummary } from "@/lib/hooks/use-usage"
import { Button } from "@/components/ui/button"
import { AgentCard } from "@/components/agent/agent-card"
import { AgentCardSkeleton } from "@/components/agent/agent-card-skeleton"
import { EmptyState } from "@/components/shared/empty-state"
import { PageHeader } from "@/components/shared/page-header"

export default function DashboardPage() {
  const { data: agents, isLoading: agentsLoading } = useAgents()
  const { data: usage } = useUsageSummary()

  return (
    <div className="flex flex-1 flex-col gap-6 p-6">
      <PageHeader
        title="내 에이전트"
        action={
          <Button render={<Link href="/agents/new" />}>
            <PlusIcon className="size-4" data-icon="inline-start" />
            새 에이전트
          </Button>
        }
      />

      {agentsLoading ? (
        <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
          {Array.from({ length: 3 }).map((_, i) => (
            <AgentCardSkeleton key={i} />
          ))}
        </div>
      ) : agents && agents.length > 0 ? (
        <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
          {agents.map((agent) => (
            <AgentCard key={agent.id} agent={agent} />
          ))}
        </div>
      ) : (
        <EmptyState
          icon={<BotIcon className="size-6" />}
          title="아직 에이전트가 없습니다."
          description="새로 만들어보세요!"
          action={
            <Button render={<Link href="/agents/new" />}>
              <PlusIcon className="size-4" data-icon="inline-start" />
              첫 에이전트 만들기
            </Button>
          }
        />
      )}

      {usage && (usage.total_tokens > 0) && (
        <div className="rounded-xl border bg-muted/30 p-4">
          <h2 className="mb-2 text-sm font-medium text-muted-foreground">
            이번 달 사용량
          </h2>
          <div className="flex items-center gap-6 text-sm">
            <div>
              <span className="text-muted-foreground">총 토큰: </span>
              <span className="font-medium">
                {usage.total_tokens.toLocaleString()}
              </span>
            </div>
            <div>
              <span className="text-muted-foreground">추정 비용: </span>
              <span className="font-medium">
                ${usage.estimated_cost_usd.toFixed(2)}
              </span>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}
