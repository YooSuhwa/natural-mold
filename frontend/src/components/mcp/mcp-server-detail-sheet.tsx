'use client'

import { useState } from 'react'
import { toast } from 'sonner'
import { Activity, RefreshCw, Trash2 } from 'lucide-react'

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
import { CredentialPicker } from '@/components/credential/credential-picker'
import { McpToolTable } from './mcp-tool-table'
import {
  useDeleteMcpServer,
  useDiscoverMcpTools,
  useMcpServer,
  useTestMcpServer,
  useUpdateMcpServer,
} from '@/lib/hooks/use-mcp-servers'

interface McpServerDetailSheetProps {
  serverId: string | null
  open: boolean
  onOpenChange: (open: boolean) => void
}

export function McpServerDetailSheet({
  serverId,
  open,
  onOpenChange,
}: McpServerDetailSheetProps) {
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
              <CredentialPicker
                value={server.credential_id}
                onChange={handleCredentialChange}
              />
            </div>

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
