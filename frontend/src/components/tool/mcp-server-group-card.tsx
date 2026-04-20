'use client'

import { useState } from 'react'
import { useTranslations } from 'next-intl'
import { toast } from 'sonner'
import {
  ChevronDownIcon,
  ChevronRightIcon,
  KeyIcon,
  LinkIcon,
  MoreVerticalIcon,
  PencilIcon,
  Trash2Icon,
  CheckCircleIcon,
} from 'lucide-react'

import { Card, CardHeader } from '@/components/ui/card'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import {
  DropdownMenu,
  DropdownMenuTrigger,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
} from '@/components/ui/dropdown-menu'
import { DeleteConfirmDialog } from '@/components/shared/delete-confirm-dialog'
import { ConnectionBindingDialog } from '@/components/connection/connection-binding-dialog'
import { MCPServerRenameDialog } from '@/components/tool/mcp-server-rename-dialog'
import { useDeleteMCPServer } from '@/lib/hooks/use-tools'
import type { MCPServerListItem, Tool } from '@/lib/types'

interface MCPServerGroupCardProps {
  server: MCPServerListItem
  tools: Tool[]
  defaultOpen?: boolean
}

export function MCPServerGroupCard({ server, tools, defaultOpen = false }: MCPServerGroupCardProps) {
  const t = useTranslations('tool.mcpServer')
  const tc = useTranslations('common')
  const [open, setOpen] = useState(defaultOpen)
  const [authOpen, setAuthOpen] = useState(false)
  const [renameOpen, setRenameOpen] = useState(false)
  const [deleteOpen, setDeleteOpen] = useState(false)
  const deleteServer = useDeleteMCPServer()

  function handleDelete() {
    deleteServer.mutate(server.id, {
      onSuccess: () => setDeleteOpen(false),
      onError: () => toast.error(t('toast.deleteFailed')),
    })
  }

  return (
    <>
      <Card className="group transition-colors hover:border-primary/40">
        <CardHeader className="grid-cols-[auto_1fr_auto] items-center gap-3">
          <button
            type="button"
            onClick={() => setOpen((v) => !v)}
            aria-expanded={open}
            aria-label={server.name}
            className="flex size-9 items-center justify-center rounded-lg bg-primary/10 text-primary cursor-pointer transition-transform"
          >
            {open ? (
              <ChevronDownIcon className="size-4" />
            ) : (
              <ChevronRightIcon className="size-4" />
            )}
          </button>

          <button
            type="button"
            onClick={() => setOpen((v) => !v)}
            className="flex flex-col items-start gap-1 text-left cursor-pointer overflow-hidden"
          >
            <div className="flex items-center gap-2">
              <span className="font-heading text-sm font-medium leading-snug truncate">
                {server.name}
              </span>
              <span className="shrink-0 text-xs text-muted-foreground">
                {t('toolCount', { count: server.tool_count })}
              </span>
            </div>
            <div className="flex w-full items-center gap-1.5 text-[11px] text-muted-foreground">
              <LinkIcon className="size-3 shrink-0" />
              <span className="truncate">{server.url}</span>
            </div>
          </button>

          <div className="flex items-center gap-2 shrink-0">
            {server.credential ? (
              <Badge className="bg-emerald-100 text-emerald-700 hover:bg-emerald-100 gap-1">
                <CheckCircleIcon className="size-3" />
                {server.credential.name}
              </Badge>
            ) : (
              <Badge className="bg-amber-100 text-amber-700 hover:bg-amber-100 gap-1">
                <KeyIcon className="size-3" />
                {t('authNotSet')}
              </Badge>
            )}

            <DropdownMenu>
              <DropdownMenuTrigger
                render={<Button variant="ghost" size="icon-xs" />}
                className="shrink-0"
                onClick={(e) => e.stopPropagation()}
              >
                <MoreVerticalIcon className="size-3.5" />
              </DropdownMenuTrigger>
              <DropdownMenuContent align="end" side="bottom" sideOffset={4}>
                <DropdownMenuItem onClick={() => setAuthOpen(true)}>
                  <KeyIcon />
                  {t('menu.auth')}
                </DropdownMenuItem>
                <DropdownMenuItem onClick={() => setRenameOpen(true)}>
                  <PencilIcon />
                  {t('menu.rename')}
                </DropdownMenuItem>
                <DropdownMenuSeparator />
                <DropdownMenuItem variant="destructive" onClick={() => setDeleteOpen(true)}>
                  <Trash2Icon />
                  {t('menu.delete')}
                </DropdownMenuItem>
              </DropdownMenuContent>
            </DropdownMenu>
          </div>
        </CardHeader>

        {open && tools.length > 0 && (
          <div className="border-t px-4 py-3">
            <div className="grid gap-2 sm:grid-cols-2 lg:grid-cols-3">
              {tools.map((tool) => (
                <MCPToolSubCard key={tool.id} tool={tool} />
              ))}
            </div>
          </div>
        )}
      </Card>

      <ConnectionBindingDialog
        type="mcp"
        mcpServerId={server.id}
        serverName={server.name}
        currentCredentialId={server.credential_id}
        triggerContext="tool-edit"
        open={authOpen}
        onOpenChange={setAuthOpen}
      />
      <MCPServerRenameDialog server={server} open={renameOpen} onOpenChange={setRenameOpen} />
      <DeleteConfirmDialog
        open={deleteOpen}
        onOpenChange={setDeleteOpen}
        title={t('delete.title')}
        description={`${server.name}\n${t('delete.warning', { count: server.tool_count })}`}
        cancelLabel={tc('cancel')}
        confirmLabel={tc('delete')}
        isPending={deleteServer.isPending}
        onConfirm={handleDelete}
      />
    </>
  )
}

function MCPToolSubCard({ tool }: { tool: Tool }) {
  return (
    <div className="rounded-lg border bg-card/50 px-3 py-2">
      <div className="text-sm font-medium truncate">{tool.name}</div>
      {tool.description && (
        <div className="mt-0.5 text-xs text-muted-foreground line-clamp-2">{tool.description}</div>
      )}
    </div>
  )
}
