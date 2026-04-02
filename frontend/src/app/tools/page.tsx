"use client"

import {
  PlusIcon,
  WrenchIcon,
  LinkIcon,
  GlobeIcon,
  Trash2Icon,
  Loader2Icon,
} from "lucide-react"
import { useTools, useDeleteTool } from "@/lib/hooks/use-tools"
import { Button } from "@/components/ui/button"
import { Card, CardContent } from "@/components/ui/card"
import { Badge } from "@/components/ui/badge"
import { Skeleton } from "@/components/ui/skeleton"
import { EmptyState } from "@/components/shared/empty-state"
import { PageHeader } from "@/components/shared/page-header"
import { AddToolDialog } from "@/components/tool/add-tool-dialog"

export default function ToolsPage() {
  const { data: tools, isLoading } = useTools()
  const deleteTool = useDeleteTool()

  const mcpTools = tools?.filter((t) => t.type === "mcp") ?? []
  const customTools = tools?.filter((t) => t.type === "custom") ?? []

  return (
    <div className="flex flex-1 flex-col gap-6 p-6">
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

      {isLoading ? (
        <div className="space-y-6">
          {Array.from({ length: 3 }).map((_, i) => (
            <Skeleton key={i} className="h-20 w-full" />
          ))}
        </div>
      ) : tools && tools.length > 0 ? (
        <div className="space-y-8">
          {mcpTools.length > 0 && (
            <div className="space-y-3">
              <h2 className="flex items-center gap-2 text-sm font-medium text-muted-foreground">
                <LinkIcon className="size-4" />
                MCP 서버
              </h2>
              <div className="space-y-2">
                {mcpTools.map((tool) => (
                  <Card key={tool.id}>
                    <CardContent className="flex items-center justify-between py-3">
                      <div className="flex items-center gap-3">
                        <div className="flex size-9 items-center justify-center rounded-lg bg-primary/10 text-primary">
                          <LinkIcon className="size-4" />
                        </div>
                        <div>
                          <div className="flex items-center gap-2">
                            <span className="text-sm font-medium">
                              {tool.name}
                            </span>
                            <Badge variant="secondary">MCP</Badge>
                          </div>
                          {tool.api_url && (
                            <p className="text-xs text-muted-foreground">
                              {tool.api_url}
                            </p>
                          )}
                        </div>
                      </div>
                      <Button
                        variant="ghost"
                        size="icon-sm"
                        aria-label={`${tool.name} 삭제`}
                        onClick={() => deleteTool.mutate(tool.id)}
                        disabled={deleteTool.isPending}
                      >
                        {deleteTool.isPending ? (
                          <Loader2Icon className="size-4 animate-spin" />
                        ) : (
                          <Trash2Icon className="size-4 text-muted-foreground" />
                        )}
                      </Button>
                    </CardContent>
                  </Card>
                ))}
              </div>
            </div>
          )}

          {customTools.length > 0 && (
            <div className="space-y-3">
              <h2 className="flex items-center gap-2 text-sm font-medium text-muted-foreground">
                <WrenchIcon className="size-4" />
                커스텀 도구
              </h2>
              <div className="space-y-2">
                {customTools.map((tool) => (
                  <Card key={tool.id}>
                    <CardContent className="flex items-center justify-between py-3">
                      <div className="flex items-center gap-3">
                        <div className="flex size-9 items-center justify-center rounded-lg bg-muted text-muted-foreground">
                          <GlobeIcon className="size-4" />
                        </div>
                        <div>
                          <div className="flex items-center gap-2">
                            <span className="text-sm font-medium">
                              {tool.name}
                            </span>
                            {tool.http_method && (
                              <Badge variant="outline">
                                {tool.http_method}
                              </Badge>
                            )}
                            {tool.auth_type && tool.auth_type !== "none" && (
                              <Badge variant="secondary">
                                {tool.auth_type}
                              </Badge>
                            )}
                          </div>
                          {tool.api_url && (
                            <p className="text-xs text-muted-foreground truncate max-w-md">
                              {tool.api_url}
                            </p>
                          )}
                        </div>
                      </div>
                      <Button
                        variant="ghost"
                        size="icon-sm"
                        aria-label={`${tool.name} 삭제`}
                        onClick={() => deleteTool.mutate(tool.id)}
                        disabled={deleteTool.isPending}
                      >
                        {deleteTool.isPending ? (
                          <Loader2Icon className="size-4 animate-spin" />
                        ) : (
                          <Trash2Icon className="size-4 text-muted-foreground" />
                        )}
                      </Button>
                    </CardContent>
                  </Card>
                ))}
              </div>
            </div>
          )}
        </div>
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
