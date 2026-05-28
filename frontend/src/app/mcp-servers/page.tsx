'use client'

import { useMemo, useState } from 'react'
import { toast } from 'sonner'
import type { ColumnDef } from '@tanstack/react-table'
import { Activity, Download, Plus, Server, Upload } from 'lucide-react'

import { announceHealthResult } from '@/lib/health-check-toast'

import { Button } from '@/components/ui/button'
import { PageHeader } from '@/components/shared/page-header'
import { DataTable } from '@/components/ui/data-table'
import { StatusChip } from '@/components/shared/status-chip'
import { EmptyState } from '@/components/shared/empty-state'
import { McpServerWizard } from '@/components/mcp/mcp-server-wizard'
import { McpServerDetailDialog } from '@/components/mcp/mcp-server-detail-dialog'
import { McpImportDialog } from '@/components/mcp/mcp-import-dialog'
import { useExportMcpServers, useMcpServers } from '@/lib/hooks/use-mcp-servers'
import { useMcpHealth, useRunHealthCheck } from '@/lib/hooks/use-health'
import type { McpServer } from '@/lib/types/mcp'
import type { HealthCheckEntry } from '@/lib/types/health'

function formatDate(value: string | null): string {
  if (!value) return '—'
  return new Date(value).toLocaleString()
}

export default function McpServersPage() {
  const { data: servers, isLoading } = useMcpServers()
  const { data: healthEntries } = useMcpHealth()
  const runHealthCheck = useRunHealthCheck()
  const [wizardOpen, setWizardOpen] = useState(false)
  const [importOpen, setImportOpen] = useState(false)
  const [detailId, setDetailId] = useState<string | null>(null)
  const exportMutation = useExportMcpServers()

  async function handleExport() {
    try {
      const data = await exportMutation.mutateAsync()
      const blob = new Blob([JSON.stringify(data, null, 2)], {
        type: 'application/json',
      })
      const url = URL.createObjectURL(blob)
      const a = document.createElement('a')
      a.href = url
      a.download = `mcp-servers-${new Date().toISOString().slice(0, 10)}.json`
      document.body.appendChild(a)
      a.click()
      a.remove()
      URL.revokeObjectURL(url)
      const count = Object.keys(data.mcpServers).length
      toast.success(`서버 ${count}개를 내보냈어요`)
    } catch (e) {
      toast.error(e instanceof Error ? e.message : '내보내기 실패')
    }
  }

  // Latest health probe per server. Falls back to the server's own
  // `status` field when no probe row exists yet.
  const healthByServer = useMemo(() => {
    const map = new Map<string, HealthCheckEntry>()
    ;(healthEntries ?? []).forEach((h) => map.set(h.target_id, h))
    return map
  }, [healthEntries])

  async function handleCheckNow(serverId: string) {
    try {
      const result = await runHealthCheck.mutateAsync({
        targetKind: 'mcp_server',
        targetId: serverId,
      })
      announceHealthResult(result)
    } catch (e) {
      toast.error(e instanceof Error ? e.message : '상태 확인 실패')
    }
  }

  const columns = useMemo<ColumnDef<McpServer>[]>(
    () => [
      {
        accessorKey: 'name',
        header: '이름',
        cell: ({ row }) => <span className="font-medium">{row.original.name}</span>,
      },
      {
        accessorKey: 'transport',
        header: '전송 방식',
        cell: ({ row }) => (
          <span className="text-xs text-muted-foreground">{row.original.transport}</span>
        ),
      },
      {
        accessorKey: 'last_tool_count',
        header: '도구',
        cell: ({ row }) => row.original.last_tool_count ?? 0,
      },
      {
        id: 'status',
        header: '상태',
        cell: ({ row }) => {
          const entry = healthByServer.get(row.original.id)
          // Health probe wins if available; otherwise the static MCP status.
          if (entry) {
            return (
              <div className="flex flex-col items-start gap-0.5">
                <StatusChip variant={entry.status} />
                <span className="text-[10px] text-muted-foreground">
                  {formatRelativeTime(entry.checked_at)}
                </span>
              </div>
            )
          }
          return <StatusChip variant={row.original.status} />
        },
      },
      {
        accessorKey: 'last_pinged_at',
        header: '최근 응답',
        cell: ({ row }) => (
          <span className="text-xs text-muted-foreground">
            {formatDate(row.original.last_pinged_at)}
          </span>
        ),
      },
      {
        id: 'actions',
        header: '',
        cell: ({ row }) => (
          <Button
            variant="ghost"
            size="sm"
            aria-label={`${row.original.name} 상태 확인`}
            data-testid={`check-now-${row.original.id}`}
            onClick={(e) => {
              e.stopPropagation()
              handleCheckNow(row.original.id)
            }}
            disabled={runHealthCheck.isPending}
          >
            <Activity className="size-3.5" /> 상태 확인
          </Button>
        ),
        enableSorting: false,
      },
    ],
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [healthByServer, runHealthCheck.isPending],
  )

  const data = servers ?? []

  return (
    <div className="flex flex-1 flex-col overflow-auto bg-gradient-to-b from-emerald-50/40 via-background to-background dark:from-emerald-950/15 dark:via-background dark:to-background">
      <div className="mx-auto flex w-full max-w-[1180px] flex-1 flex-col gap-6 px-6 py-7 pb-20 md:px-8">
        <PageHeader
          title="MCP 서버"
          description="MCP 서버를 연결해 원격 도구를 가져오세요."
          action={
            <div className="flex flex-wrap items-center gap-2">
              <Button variant="outline" onClick={() => setImportOpen(true)}>
                <Download className="size-4" />
                불러오기
              </Button>
              <Button variant="outline" onClick={handleExport} disabled={exportMutation.isPending}>
                <Upload className="size-4" />
                내보내기
              </Button>
              <Button onClick={() => setWizardOpen(true)}>
                <Plus className="size-4" />새 MCP 서버
              </Button>
            </div>
          }
        />

        {!isLoading && data.length === 0 ? (
          <EmptyState
            icon={<Server className="size-6" />}
            title="아직 MCP 서버가 없어요"
            description="서버를 추가하면 원격 도구를 가져와 자격증명에 연결할 수 있어요."
            action={
              <Button onClick={() => setWizardOpen(true)}>
                <Plus className="size-4" />
                서버 추가
              </Button>
            }
          />
        ) : (
          <DataTable
            columns={columns}
            data={data}
            loading={isLoading}
            searchable
            searchPlaceholder="서버 검색"
            onRowClick={(row) => setDetailId(row.id)}
            emptyTitle="검색 결과가 없어요"
          />
        )}

        <McpServerWizard open={wizardOpen} onOpenChange={setWizardOpen} />
        <McpImportDialog open={importOpen} onOpenChange={setImportOpen} />
        <McpServerDetailDialog
          serverId={detailId}
          open={!!detailId}
          onOpenChange={(open) => !open && setDetailId(null)}
        />
      </div>
    </div>
  )
}

function formatRelativeTime(iso: string): string {
  const then = new Date(iso).getTime()
  if (Number.isNaN(then)) return iso
  const deltaSec = Math.floor((Date.now() - then) / 1000)
  if (deltaSec < 60) return `${deltaSec}초 전`
  if (deltaSec < 3600) return `${Math.floor(deltaSec / 60)}분 전`
  if (deltaSec < 86400) return `${Math.floor(deltaSec / 3600)}시간 전`
  return `${Math.floor(deltaSec / 86400)}일 전`
}
