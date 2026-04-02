"use client"

import { useState, useMemo } from "react"
import {
  PlusIcon,
  WrenchIcon,
  LinkIcon,
  GlobeIcon,
  Trash2Icon,
  Loader2Icon,
  SparklesIcon,
  KeyIcon,
  CheckCircleIcon,
  PlugIcon,
  SearchIcon,
  ShieldCheckIcon,
  ServerIcon,
} from "lucide-react"
import { useTools, useDeleteTool } from "@/lib/hooks/use-tools"
import { Button } from "@/components/ui/button"
import { Card, CardHeader, CardTitle, CardDescription, CardContent, CardFooter } from "@/components/ui/card"
import { Badge } from "@/components/ui/badge"
import { Skeleton } from "@/components/ui/skeleton"
import { Input } from "@/components/ui/input"
import { EmptyState } from "@/components/shared/empty-state"
import { PageHeader } from "@/components/shared/page-header"
import { AddToolDialog } from "@/components/tool/add-tool-dialog"
import { PrebuiltAuthDialog } from "@/components/tool/prebuilt-auth-dialog"
import type { Tool } from "@/lib/types"

type ToolFilter = "all" | "builtin" | "prebuilt" | "mcp" | "custom"

const FILTER_OPTIONS: { value: ToolFilter; label: string }[] = [
  { value: "all", label: "All" },
  { value: "builtin", label: "Built-in" },
  { value: "prebuilt", label: "Pre-built" },
  { value: "custom", label: "Custom" },
  { value: "mcp", label: "MCP" },
]

const TOOL_META: Record<string, { icon: typeof SparklesIcon; color: string; badge: string; badgeClass: string }> = {
  builtin: {
    icon: SparklesIcon,
    color: "bg-violet-500/10 text-violet-600",
    badge: "Built-in",
    badgeClass: "bg-violet-100 text-violet-700 hover:bg-violet-100",
  },
  prebuilt: {
    icon: PlugIcon,
    color: "bg-sky-500/10 text-sky-600",
    badge: "Pre-built",
    badgeClass: "bg-sky-100 text-sky-700 hover:bg-sky-100",
  },
  mcp: {
    icon: LinkIcon,
    color: "bg-primary/10 text-primary",
    badge: "MCP",
    badgeClass: "",
  },
  custom: {
    icon: GlobeIcon,
    color: "bg-muted text-muted-foreground",
    badge: "Custom",
    badgeClass: "",
  },
}

type PrebuiltStatus = "not_configured" | "server_key" | "configured"

function getPrebuiltStatus(tool: Tool): PrebuiltStatus {
  const hasAuth = tool.auth_config && Object.values(tool.auth_config).some((v) => typeof v === "string" && v.length > 0)
  if (hasAuth) return "configured"
  if (tool.server_key_available) return "server_key"
  return "not_configured"
}

const PREBUILT_STYLES: Record<PrebuiltStatus, { color: string; badgeClass: string; badge: string; icon: typeof KeyIcon; buttonLabel: string }> = {
  not_configured: {
    color: "bg-amber-500/10 text-amber-600",
    badgeClass: "bg-amber-100 text-amber-700 hover:bg-amber-100",
    badge: "키 미설정",
    icon: KeyIcon,
    buttonLabel: "키 설정",
  },
  server_key: {
    color: "bg-sky-500/10 text-sky-600",
    badgeClass: "bg-sky-100 text-sky-700 hover:bg-sky-100",
    badge: "서버 설정",
    icon: ServerIcon,
    buttonLabel: "개별 키 설정",
  },
  configured: {
    color: "bg-emerald-500/10 text-emerald-600",
    badgeClass: "bg-emerald-100 text-emerald-700 hover:bg-emerald-100",
    badge: "설정 완료",
    icon: CheckCircleIcon,
    buttonLabel: "키 변경",
  },
}

function ToolCardSkeleton() {
  return (
    <Card className="h-full">
      <CardHeader>
        <div className="flex items-start justify-between">
          <Skeleton className="size-9 rounded-lg" />
          <Skeleton className="h-5 w-14" />
        </div>
        <Skeleton className="mt-2 h-5 w-32" />
        <Skeleton className="h-4 w-full" />
      </CardHeader>
    </Card>
  )
}

function ToolCard({ tool, onDelete, isDeleting }: { tool: Tool; onDelete: (id: string) => void; isDeleting: boolean }) {
  const meta = TOOL_META[tool.type] ?? TOOL_META.custom
  const Icon = meta.icon
  const isPrebuilt = tool.type === "prebuilt"
  const prebuiltStatus = isPrebuilt ? getPrebuiltStatus(tool) : null
  const pStyle = prebuiltStatus ? PREBUILT_STYLES[prebuiltStatus] : null
  const isDeletable = !tool.is_system

  return (
    <Card className="group h-full flex flex-col transition-colors hover:border-primary/40">
      <CardHeader className="flex-1">
        <div className="flex items-start justify-between">
          <div className={`flex size-9 items-center justify-center rounded-lg ${
            pStyle ? pStyle.color : meta.color
          }`}>
            {pStyle ? (
              <pStyle.icon className="size-4" />
            ) : (
              <Icon className="size-4" />
            )}
          </div>
          <Badge className={
            pStyle ? pStyle.badgeClass : meta.badgeClass
          } variant={pStyle || meta.badgeClass ? undefined : "secondary"}>
            {pStyle ? pStyle.badge : meta.badge}
          </Badge>
        </div>
        <CardTitle className="mt-2 text-sm">{tool.name}</CardTitle>
        {tool.description && (
          <CardDescription className="line-clamp-2 text-xs">
            {tool.description}
          </CardDescription>
        )}
      </CardHeader>

      <CardFooter className="gap-2">
        {isPrebuilt && pStyle ? (
          <PrebuiltAuthDialog
            tool={tool}
            trigger={
              <Button variant="outline" size="sm" className="w-full cursor-pointer">
                <KeyIcon className="size-3.5" data-icon="inline-start" />
                {pStyle.buttonLabel}
              </Button>
            }
          />
        ) : isDeletable ? (
          <Button
            variant="ghost"
            size="sm"
            className="w-full text-muted-foreground hover:text-destructive"
            onClick={() => onDelete(tool.id)}
            disabled={isDeleting}
          >
            {isDeleting ? (
              <Loader2Icon className="size-3.5 animate-spin" data-icon="inline-start" />
            ) : (
              <Trash2Icon className="size-3.5" data-icon="inline-start" />
            )}
            삭제
          </Button>
        ) : (
          <div className="flex w-full items-center justify-center gap-1.5 text-xs text-muted-foreground">
            <ShieldCheckIcon className="size-3.5" />
            <span>시스템 도구</span>
          </div>
        )}
      </CardFooter>
    </Card>
  )
}

export default function ToolsPage() {
  const { data: tools, isLoading } = useTools()
  const deleteTool = useDeleteTool()
  const [search, setSearch] = useState("")
  const [filter, setFilter] = useState<ToolFilter>("all")

  const filteredTools = useMemo(() => {
    if (!tools) return []
    return tools.filter((t) => {
      if (filter !== "all" && t.type !== filter) return false
      if (search) {
        const q = search.toLowerCase()
        const nameMatch = t.name.toLowerCase().includes(q)
        const descMatch = t.description?.toLowerCase().includes(q) ?? false
        if (!nameMatch && !descMatch) return false
      }
      return true
    })
  }, [tools, filter, search])

  const counts = useMemo(() => {
    if (!tools) return { all: 0, builtin: 0, prebuilt: 0, mcp: 0, custom: 0 }
    return {
      all: tools.length,
      builtin: tools.filter((t) => t.type === "builtin").length,
      prebuilt: tools.filter((t) => t.type === "prebuilt").length,
      mcp: tools.filter((t) => t.type === "mcp" && !t.is_system).length,
      custom: tools.filter((t) => t.type === "custom" && !t.is_system).length,
    }
  }, [tools])

  return (
    <div className="flex flex-1 flex-col gap-6 overflow-auto p-6">
      <PageHeader
        title="도구 관리"
        action={
          <AddToolDialog
            trigger={
              <Button>
                <PlusIcon className="size-4" data-icon="inline-start" />
                도구 추가
              </Button>
            }
          />
        }
      />

      <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
        <div className="flex items-center gap-1.5 flex-wrap">
          {FILTER_OPTIONS.map((opt) => (
            <Button
              key={opt.value}
              variant={filter === opt.value ? "default" : "outline"}
              size="sm"
              onClick={() => setFilter(opt.value)}
              className="cursor-pointer"
            >
              {opt.label}
              {counts[opt.value] > 0 && (
                <span className={`ml-1 text-xs ${filter === opt.value ? "text-primary-foreground/70" : "text-muted-foreground"}`}>
                  {counts[opt.value]}
                </span>
              )}
            </Button>
          ))}
        </div>
        <div className="relative w-full sm:w-64">
          <SearchIcon className="absolute left-2.5 top-1/2 size-4 -translate-y-1/2 text-muted-foreground" />
          <Input
            placeholder="도구 검색..."
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            className="pl-9"
          />
        </div>
      </div>

      {isLoading ? (
        <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4">
          {Array.from({ length: 8 }).map((_, i) => (
            <ToolCardSkeleton key={i} />
          ))}
        </div>
      ) : filteredTools.length > 0 ? (
        <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4">
          {filteredTools.map((tool) => (
            <ToolCard
              key={tool.id}
              tool={tool}
              onDelete={(id) => deleteTool.mutate(id)}
              isDeleting={deleteTool.isPending}
            />
          ))}
        </div>
      ) : search || filter !== "all" ? (
        <EmptyState
          icon={<SearchIcon className="size-6" />}
          title="검색 결과가 없습니다."
          description={search ? `"${search}"에 해당하는 도구를 찾을 수 없습니다.` : "선택한 필터에 해당하는 도구가 없습니다."}
          action={
            <Button variant="outline" onClick={() => { setSearch(""); setFilter("all") }}>
              필터 초기화
            </Button>
          }
        />
      ) : (
        <EmptyState
          icon={<WrenchIcon className="size-6" />}
          title="등록된 도구가 없습니다."
          description="도구를 추가하여 에이전트에 연결하세요."
          action={
            <AddToolDialog
              trigger={
                <Button>
                  <PlusIcon className="size-4" data-icon="inline-start" />
                  도구 추가
                </Button>
              }
            />
          }
        />
      )}
    </div>
  )
}
