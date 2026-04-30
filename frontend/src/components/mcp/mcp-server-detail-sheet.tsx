'use client'

import { useMemo, useState } from 'react'
import { toast } from 'sonner'
import { Activity, Loader2, RefreshCw, Trash2 } from 'lucide-react'

import { announceHealthResult } from '@/lib/health-check-toast'

import {
  Sheet,
  SheetContent,
  SheetDescription,
  SheetHeader,
  SheetTitle,
} from '@/components/ui/sheet'
import { Button } from '@/components/ui/button'
import { Separator } from '@/components/ui/separator'
import { StatusChip } from '@/components/shared/status-chip'
import { HealthHistoryChart } from '@/components/shared/health-history-chart'
import { CredentialPicker } from '@/components/credential/credential-picker'
import { McpToolTable } from './mcp-tool-table'
import {
  useDeleteMcpServer,
  useDiscoverMcpTools,
  useMcpServer,
  useTestMcpServer,
  useUpdateMcpServer,
} from '@/lib/hooks/use-mcp-servers'
import { useMcpHealth, useRunHealthCheck } from '@/lib/hooks/use-health'

interface McpServerDetailSheetProps {
  serverId: string | null
  open: boolean
  onOpenChange: (open: boolean) => void
}

export function McpServerDetailSheet({ serverId, open, onOpenChange }: McpServerDetailSheetProps) {
  const { data: server } = useMcpServer(serverId)
  const update = useUpdateMcpServer()
  const remove = useDeleteMcpServer()
  const test = useTestMcpServer()
  const discover = useDiscoverMcpTools()
  const [confirming, setConfirming] = useState(false)

  async function handleTest() {
    if (!server) return
    try {
      const result = await test.mutateAsync(server.id)
      if (result.success) {
        toast.success(`Connected (${result.tool_count} tools)`)
      } else {
        toast.error(result.error ?? 'Connection failed')
      }
    } catch (e) {
      toast.error(e instanceof Error ? e.message : 'Test failed')
    }
  }

  async function handleDiscover() {
    if (!server) return
    try {
      const result = await discover.mutateAsync(server.id)
      if (result.success) {
        toast.success(`Imported ${result.tools.length} tool(s)`)
      } else {
        toast.error(result.error ?? 'Discovery failed')
      }
    } catch (e) {
      toast.error(e instanceof Error ? e.message : 'Discovery failed')
    }
  }

  async function handleDelete() {
    if (!server) return
    try {
      await remove.mutateAsync(server.id)
      toast.success('Server deleted')
      onOpenChange(false)
    } catch (e) {
      toast.error(e instanceof Error ? e.message : 'Delete failed')
    }
  }

  async function handleCredentialChange(next: string | null) {
    if (!server) return
    try {
      await update.mutateAsync({ id: server.id, data: { credential_id: next } })
      toast.success('Credential updated')
    } catch (e) {
      toast.error(e instanceof Error ? e.message : 'Update failed')
    }
  }

  return (
    <Sheet open={open} onOpenChange={onOpenChange}>
      <SheetContent className="w-full sm:max-w-md flex flex-col gap-4 overflow-y-auto p-0">
        <SheetHeader className="border-b">
          {server ? (
            <>
              <SheetTitle>{server.name}</SheetTitle>
              <SheetDescription className="flex items-center gap-2">
                <StatusChip variant={server.status} />
                <span className="text-xs">{server.transport}</span>
              </SheetDescription>
            </>
          ) : (
            <SheetTitle>Loading...</SheetTitle>
          )}
        </SheetHeader>

        {server && (
          <div className="flex-1 px-4 pb-4 space-y-4">
            <div className="flex flex-wrap gap-2">
              <Button size="sm" variant="outline" onClick={handleTest} disabled={test.isPending}>
                <Activity className="size-3.5" /> Test
              </Button>
              <Button
                size="sm"
                variant="outline"
                onClick={handleDiscover}
                disabled={discover.isPending}
              >
                <RefreshCw className="size-3.5" /> Refresh tools
              </Button>
              <Button
                size="sm"
                variant="outline"
                className="text-destructive hover:text-destructive"
                onClick={() => setConfirming(true)}
              >
                <Trash2 className="size-3.5" /> Delete
              </Button>
            </div>

            <Separator />

            <div className="space-y-1.5">
              <label className="text-xs font-medium">Endpoint</label>
              <div className="rounded border bg-muted/40 p-2 text-xs font-mono break-all">
                {server.transport === 'stdio' ? server.command : server.url}
              </div>
            </div>

            <div className="space-y-1.5">
              <label className="text-xs font-medium">Credential</label>
              <CredentialPicker value={server.credential_id} onChange={handleCredentialChange} />
            </div>

            <Separator />

            <McpHealthSection serverId={server.id} />

            <Separator />

            <div className="space-y-1.5">
              <h3 className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">
                Imported tools ({server.tools.length})
              </h3>
              <McpToolTable tools={server.tools} />
            </div>

            {confirming && (
              <div className="rounded border border-destructive/40 bg-destructive/5 p-3 text-xs">
                <p className="font-medium text-destructive">Delete this server?</p>
                <div className="mt-2 flex gap-2">
                  <Button size="sm" variant="outline" onClick={() => setConfirming(false)}>
                    Cancel
                  </Button>
                  <Button
                    size="sm"
                    variant="destructive"
                    onClick={handleDelete}
                    disabled={remove.isPending}
                  >
                    Confirm delete
                  </Button>
                </div>
              </div>
            )}
          </div>
        )}
      </SheetContent>
    </Sheet>
  )
}

function McpHealthSection({ serverId }: { serverId: string }) {
  const { data: healthEntries } = useMcpHealth()
  const runHealthCheck = useRunHealthCheck()
  const latest = useMemo(
    () => (healthEntries ?? []).find((h) => h.target_id === serverId),
    [healthEntries, serverId],
  )

  async function handleCheck() {
    try {
      const result = await runHealthCheck.mutateAsync({
        targetKind: 'mcp_server',
        targetId: serverId,
      })
      announceHealthResult(result)
    } catch (e) {
      toast.error(e instanceof Error ? e.message : 'Health check failed')
    }
  }

  return (
    <div className="space-y-2">
      <div className="flex items-center justify-between">
        <h3 className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">
          Health
        </h3>
        <Button
          size="sm"
          variant="outline"
          onClick={handleCheck}
          disabled={runHealthCheck.isPending}
          data-testid="mcp-health-check-now"
        >
          {runHealthCheck.isPending ? (
            <Loader2 className="size-3.5 animate-spin" />
          ) : (
            <Activity className="size-3.5" />
          )}
          Check now
        </Button>
      </div>
      {latest && (
        <div className="flex items-center gap-2 rounded-md border bg-muted/30 p-2 text-xs">
          <StatusChip variant={latest.status} />
          {typeof latest.latency_ms === 'number' && (
            <span className="text-muted-foreground">{latest.latency_ms} ms</span>
          )}
          <span className="text-[10px] text-muted-foreground">
            {new Date(latest.checked_at).toLocaleString()}
          </span>
        </div>
      )}
      <HealthHistoryChart targetKind="mcp_server" targetId={serverId} />
    </div>
  )
}
