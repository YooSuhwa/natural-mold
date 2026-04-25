'use client'

import { useMemo, useState } from 'react'
import { useTranslations } from 'next-intl'
import { toast } from 'sonner'
import {
  ChevronDownIcon,
  ChevronRightIcon,
  KeyIcon,
  LinkIcon,
  MoreVerticalIcon,
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
import { useDeleteConnection } from '@/lib/hooks/use-connections'
import { useCredentials } from '@/lib/hooks/use-credentials'
import type { Connection, Tool } from '@/lib/types'

interface MCPServerGroupCardProps {
  connection: Connection
  tools: Tool[]
  defaultOpen?: boolean
}

export function MCPServerGroupCard({
  connection,
  tools,
  defaultOpen = false,
}: MCPServerGroupCardProps) {
  const t = useTranslations('tool.mcpServer')
  const tc = useTranslations('common')
  const [open, setOpen] = useState(defaultOpen)
  const [authOpen, setAuthOpen] = useState(false)
  const [deleteOpen, setDeleteOpen] = useState(false)
  const deleteConnection = useDeleteConnection()
  const { data: credentials } = useCredentials()

  const credentialName = useMemo(
    () => credentials?.find((c) => c.id === connection.credential_id)?.name ?? null,
    [credentials, connection.credential_id],
  )
  const url = connection.extra_config?.url ?? ''
  const toolCount = tools.length
  // env_vars 템플릿이 있어야 credential이 실제로 인증 헤더로 주입된다.
  // v1 신규 등록 dialog는 auth_type='none'으로만 connection을 만들기 때문에
  // 이 경로의 connection은 credential을 받아도 무용지물 — 인증 설정 메뉴를
  // 비활성화해 dead-on-arrival 상태를 사용자에게 노출. m9 마이그레이션으로
  // 만들어진 기존 connection은 env_vars 템플릿을 가지고 있어 그대로 동작.
  const supportsCredentialBinding = (connection.extra_config?.env_var_keys?.length ?? 0) > 0

  // tools가 살아있을 때 connection 삭제는 tools.connection_id를 NULL로 만들어
  // 도구를 fail-closed 상태로 orphan시킨다 (chat_service._resolve_*가 NULL conn
  // 에서 ToolConfigError raise). ConnectionDetailSheet의 hasUsage 가드와 동일
  // 정책. UI 메뉴에서도 같은 보호 적용.
  const hasBoundTools = toolCount > 0

  function handleDelete() {
    if (hasBoundTools) {
      toast.error(t('delete.blockedByUsage'))
      return
    }
    deleteConnection.mutate(
      { id: connection.id, type: connection.type, provider_name: connection.provider_name },
      {
        onSuccess: () => setDeleteOpen(false),
        onError: () => toast.error(t('toast.deleteFailed')),
      },
    )
  }

  return (
    <>
      <Card className="group transition-colors hover:border-primary/40">
        <CardHeader className="grid-cols-[auto_1fr_auto] items-center gap-3">
          <button
            type="button"
            onClick={() => setOpen((v) => !v)}
            aria-expanded={open}
            aria-label={connection.display_name}
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
                {connection.display_name}
              </span>
              <span className="shrink-0 text-xs text-muted-foreground">
                {t('toolCount', { count: toolCount })}
              </span>
            </div>
            <div className="flex w-full items-center gap-1.5 text-[11px] text-muted-foreground">
              <LinkIcon className="size-3 shrink-0" />
              <span className="truncate">{url}</span>
            </div>
          </button>

          <div className="flex items-center gap-2 shrink-0">
            {credentialName ? (
              <Badge className="bg-emerald-100 text-emerald-700 hover:bg-emerald-100 gap-1">
                <CheckCircleIcon className="size-3" />
                {credentialName}
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
                <DropdownMenuItem
                  onClick={() => setAuthOpen(true)}
                  disabled={!supportsCredentialBinding}
                  title={
                    supportsCredentialBinding
                      ? undefined
                      : t('menu.authNotSupported')
                  }
                >
                  <KeyIcon />
                  {t('menu.auth')}
                </DropdownMenuItem>
                <DropdownMenuSeparator />
                <DropdownMenuItem
                  variant="destructive"
                  onClick={() => setDeleteOpen(true)}
                  disabled={hasBoundTools}
                  title={hasBoundTools ? t('delete.blockedByUsage') : undefined}
                >
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
        connectionId={connection.id}
        connectionName={connection.display_name}
        currentCredentialId={connection.credential_id}
        open={authOpen}
        onOpenChange={setAuthOpen}
      />
      <DeleteConfirmDialog
        open={deleteOpen}
        onOpenChange={setDeleteOpen}
        title={t('delete.title')}
        description={`${connection.display_name}\n${t('delete.warning', { count: toolCount })}`}
        cancelLabel={tc('cancel')}
        confirmLabel={tc('delete')}
        isPending={deleteConnection.isPending}
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
