"use client"

import { BarChart3Icon } from "lucide-react"
import { useUsageSummary } from "@/lib/hooks/use-usage"
import {
  Card,
  CardContent,
  CardHeader,
  CardTitle,
} from "@/components/ui/card"
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table"
import { Skeleton } from "@/components/ui/skeleton"
import { EmptyState } from "@/components/shared/empty-state"
import { PageHeader } from "@/components/shared/page-header"

export default function UsagePage() {
  const { data: usage, isLoading } = useUsageSummary()

  if (isLoading) {
    return (
      <div className="flex flex-1 flex-col gap-6 overflow-auto p-6">
        <PageHeader title="토큰 사용량" />
        <div className="grid gap-4 sm:grid-cols-3">
          {Array.from({ length: 3 }).map((_, i) => (
            <Skeleton key={i} className="h-24 w-full" />
          ))}
        </div>
        <Skeleton className="h-48 w-full" />
      </div>
    )
  }

  if (!usage || usage.total_tokens === 0) {
    return (
      <div className="flex flex-1 flex-col gap-6 overflow-auto p-6">
        <PageHeader title="토큰 사용량" />
        <EmptyState
          icon={<BarChart3Icon className="size-6" />}
          title="아직 사용 내역이 없습니다."
          description="에이전트를 사용하면 여기에 토큰 사용량이 표시됩니다."
        />
      </div>
    )
  }

  return (
    <div className="flex flex-1 flex-col gap-6 overflow-auto p-6">
      <PageHeader title="토큰 사용량" />

      {/* Summary cards */}
      <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
        <Card>
          <CardHeader className="pb-1">
            <CardTitle className="text-sm font-medium text-foreground/70">
              총 토큰
            </CardTitle>
          </CardHeader>
          <CardContent>
            <span className="text-2xl font-bold">
              {usage.total_tokens.toLocaleString()}
            </span>
          </CardContent>
        </Card>

        <Card>
          <CardHeader className="pb-1">
            <CardTitle className="text-sm font-medium text-foreground/70">
              추정 비용
            </CardTitle>
          </CardHeader>
          <CardContent>
            <span className="text-2xl font-bold">
              ${usage.estimated_cost_usd.toFixed(2)}
            </span>
          </CardContent>
        </Card>

        <Card>
          <CardHeader className="pb-1">
            <CardTitle className="text-sm font-medium text-foreground/70">
              입력 토큰
            </CardTitle>
          </CardHeader>
          <CardContent>
            <span className="text-2xl font-bold">
              {usage.prompt_tokens.toLocaleString()}
            </span>
          </CardContent>
        </Card>

        <Card>
          <CardHeader className="pb-1">
            <CardTitle className="text-sm font-medium text-foreground/70">
              출력 토큰
            </CardTitle>
          </CardHeader>
          <CardContent>
            <span className="text-2xl font-bold">
              {usage.completion_tokens.toLocaleString()}
            </span>
          </CardContent>
        </Card>
      </div>

      {/* Per-agent breakdown */}
      {usage.by_agent.length > 0 && (
        <Card>
          <CardHeader>
            <CardTitle>에이전트별 사용량</CardTitle>
          </CardHeader>
          <CardContent>
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>에이전트</TableHead>
                  <TableHead className="text-right">토큰</TableHead>
                  <TableHead className="text-right">비용</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {usage.by_agent.map((row) => (
                  <TableRow key={row.agent_id}>
                    <TableCell className="font-medium">
                      {row.agent_name}
                    </TableCell>
                    <TableCell className="text-right">
                      {row.total_tokens.toLocaleString()}
                    </TableCell>
                    <TableCell className="text-right">
                      ${row.estimated_cost.toFixed(2)}
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </CardContent>
        </Card>
      )}
    </div>
  )
}
