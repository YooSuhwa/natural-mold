"use client"

import Link from "next/link"
import { WrenchIcon } from "lucide-react"
import {
  Card,
  CardHeader,
  CardTitle,
  CardDescription,
  CardContent,
  CardFooter,
} from "@/components/ui/card"
import { Badge } from "@/components/ui/badge"
import type { Agent } from "@/lib/types"

interface AgentCardProps {
  agent: Agent
}

export function AgentCard({ agent }: AgentCardProps) {
  const statusColor =
    agent.status === "active"
      ? "bg-emerald-500"
      : agent.status === "error"
        ? "bg-red-500"
        : "bg-yellow-500"

  const statusLabel =
    agent.status === "active"
      ? "활성"
      : agent.status === "error"
        ? "오류"
        : "비활성"

  return (
    <Link href={`/agents/${agent.id}`} className="group">
      <Card className="h-full transition-all hover:ring-2 hover:ring-primary/20 hover:shadow-md">
        <CardHeader>
          <div className="flex items-start justify-between">
            <CardTitle className="group-hover:text-primary transition-colors">
              {agent.name}
            </CardTitle>
            <Badge variant="secondary" className="shrink-0">
              <span className={`mr-1 inline-block size-1.5 rounded-full ${statusColor}`} />
              {statusLabel}
            </Badge>
          </div>
          {agent.description && (
            <CardDescription className="line-clamp-2">
              {agent.description}
            </CardDescription>
          )}
        </CardHeader>
        <CardContent>
          <div className="flex flex-col gap-2 text-sm text-muted-foreground">
            <div className="flex items-center gap-1.5">
              <span className="font-medium text-foreground/70">모델:</span>
              <span>{agent.model.display_name}</span>
            </div>
            {agent.tools.length > 0 && (
              <div className="flex items-center gap-1.5">
                <WrenchIcon className="size-3.5" />
                <span>도구 {agent.tools.length}개</span>
              </div>
            )}
          </div>
        </CardContent>
        <CardFooter className="text-xs text-muted-foreground">
          {new Date(agent.created_at).toLocaleDateString("ko-KR")} 생성
        </CardFooter>
      </Card>
    </Link>
  )
}
