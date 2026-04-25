'use client'

import { useState } from 'react'
import { useTranslations } from 'next-intl'
import { toast } from 'sonner'
import {
  AlertTriangleIcon,
  KeyRoundIcon,
  PencilIcon,
  ServerIcon,
  Trash2Icon,
  WrenchIcon,
  XIcon,
} from 'lucide-react'

import {
  Sheet,
  SheetContent,
  SheetHeader,
  SheetTitle,
  SheetClose,
} from '@/components/ui/sheet'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { DeleteConfirmDialog } from '@/components/shared/delete-confirm-dialog'
import { useCredential } from '@/lib/hooks/use-credentials'
import { ConnectionStatusBadge } from '@/components/connection/connection-status-badge'
import {
  useConnections,
  useDeleteConnection,
  useUpdateConnection,
} from '@/lib/hooks/use-connections'
import { useToolsByConnection } from '@/lib/hooks/use-tools'
import { ConnectionBindingDialog } from '@/components/connection/connection-binding-dialog'
import { CredentialFormDialog } from '@/components/tool/credential-form-dialog'
import { isPrebuiltProviderName } from '@/lib/types'
import type { Connection } from '@/lib/types'

interface ConnectionDetailSheetProps {
  connection: Connection | null
  onOpenChange: (open: boolean) => void
}

export function ConnectionDetailSheet({
  connection,
  onOpenChange,
}: ConnectionDetailSheetProps) {
  return (
    <Sheet
      open={!!connection}
      onOpenChange={(v) => {
        if (!v) onOpenChange(false)
      }}
    >
      <SheetContent
        showCloseButton={false}
        className="flex w-full flex-col gap-0 sm:max-w-md"
      >
        {connection && <DetailBody connection={connection} onClose={() => onOpenChange(false)} />}
      </SheetContent>
    </Sheet>
  )
}

function DetailBody({ connection, onClose }: { connection: Connection; onClose: () => void }) {
  const t = useTranslations('connections.detail')
  const tCard = useTranslations('connections.card')
  const tStatus = useTranslations('connections.statusToast')
  const tc = useTranslations('common')

  const credential = useCredential(connection.credential_id)
  const { data: sameTypeConnections } = useConnections({
    type: connection.type,
    provider_name: connection.provider_name,
  })
  const tools = useToolsByConnection(connection)
  const updateConnection = useUpdateConnection()
  const deleteConnection = useDeleteConnection()

  const [rebindOpen, setRebindOpen] = useState(false)
  const [credentialEditOpen, setCredentialEditOpen] = useState(false)
  const [deleteOpen, setDeleteOpen] = useState(false)

  const Icon =
    connection.type === 'prebuilt'
      ? KeyRoundIcon
      : connection.type === 'mcp'
        ? ServerIcon
        : WrenchIcon

  const isDisabled = connection.status === 'disabled'
  const toolCount = tools.length
  const hasUsage = toolCount > 0
  const isOnlyDefaultPrebuilt =
    connection.type === 'prebuilt' &&
    connection.is_default &&
    (sameTypeConnections?.filter((c) => c.id !== connection.id).length ?? 0) === 0

  function handleStatusToggle(nextActive: boolean) {
    updateConnection.mutate(
      { id: connection.id, data: { status: nextActive ? 'active' : 'disabled' } },
      {
        onSuccess: () => toast.success(tStatus('changed')),
        onError: () => toast.error(tStatus('changeFailed')),
      },
    )
  }

  function handleDelete() {
    deleteConnection.mutate(
      { id: connection.id, type: connection.type, provider_name: connection.provider_name },
      {
        onSuccess: () => {
          toast.success(tStatus('deleted'))
          setDeleteOpen(false)
          onClose()
        },
        onError: () => toast.error(tStatus('deleteFailed')),
      },
    )
  }

  return (
    <>
      <SheetHeader className="flex-row items-center justify-between border-b px-5 py-4">
        <div className="flex items-center gap-3">
          <div className="flex size-10 items-center justify-center rounded-lg bg-muted">
            <Icon className="size-5 text-muted-foreground" />
          </div>
          <div className="min-w-0">
            <SheetTitle className="truncate">{connection.display_name}</SheetTitle>
            <div className="mt-1 flex flex-wrap items-center gap-1.5">
              <Badge variant="outline" className="text-[10px]">
                {connection.provider_name}
              </Badge>
              {connection.is_default && (
                <Badge variant="secondary" className="text-[10px]">
                  {tCard('isDefaultBadge')}
                </Badge>
              )}
              <ConnectionStatusBadge status={connection.status} />
            </div>
          </div>
        </div>
        <SheetClose
          render={<Button variant="ghost" size="icon-sm" aria-label={tc('close')} />}
        >
          <XIcon className="size-4" />
        </SheetClose>
      </SheetHeader>

      <div className="flex-1 overflow-auto px-5 py-4 space-y-6">
        {/* 개요 */}
        <section>
          <h3 className="text-sm font-semibold mb-2">{t('sectionOverview')}</h3>
          <dl className="grid grid-cols-[auto_1fr] gap-x-4 gap-y-1.5 text-sm">
            <dt className="text-muted-foreground">{t('type')}</dt>
            <dd>{connection.type.toUpperCase()}</dd>
            <dt className="text-muted-foreground">{t('provider')}</dt>
            <dd className="font-mono text-xs">{connection.provider_name}</dd>
            <dt className="text-muted-foreground">{t('createdAt')}</dt>
            <dd>{new Date(connection.created_at).toLocaleDateString()}</dd>
            <dt className="text-muted-foreground">{t('updatedAt')}</dt>
            <dd>{new Date(connection.updated_at).toLocaleDateString()}</dd>
          </dl>
        </section>

        {/* Credential */}
        <section>
          <h3 className="text-sm font-semibold mb-2">{t('sectionCredential')}</h3>
          {credential ? (
            <div className="rounded-md border bg-muted/30 px-3 py-2 text-sm">
              <div className="font-medium">{credential.name}</div>
              <div className="mt-0.5 text-xs text-muted-foreground">
                {credential.credential_type === 'oauth2' ? 'OAuth2' : 'API Key'}
                {credential.field_keys.length > 0 && (
                  <> · {credential.field_keys.join(', ')}</>
                )}
              </div>
            </div>
          ) : (
            <div className="rounded-md border border-dashed px-3 py-2 text-sm text-muted-foreground">
              {t('credentialUnbound')}
            </div>
          )}
          <div className="mt-2 space-y-2">
            <div className="flex gap-2">
              {connection.type !== 'mcp' && (
                <Button variant="outline" size="sm" onClick={() => setRebindOpen(true)}>
                  <KeyRoundIcon className="size-3.5" data-icon="inline-start" />
                  {t('changeCredential')}
                </Button>
              )}
              {credential && (
                <Button variant="outline" size="sm" onClick={() => setCredentialEditOpen(true)}>
                  <PencilIcon className="size-3.5" data-icon="inline-start" />
                  {t('editCredential')}
                </Button>
              )}
            </div>
            {connection.type === 'mcp' && (
              <p className="text-xs text-muted-foreground">{t('mcpRebindHint')}</p>
            )}
          </div>
        </section>

        {/* Usage */}
        <section>
          <h3 className="text-sm font-semibold mb-2">
            {t('sectionUsage')}{' '}
            <span className="font-normal text-muted-foreground">({toolCount})</span>
          </h3>
          {toolCount > 0 ? (
            <ul className="space-y-1 text-sm">
              {tools.map((tl) => (
                <li key={tl.id} className="truncate rounded bg-muted/30 px-2 py-1">
                  {tl.name}
                </li>
              ))}
            </ul>
          ) : (
            <p className="text-sm text-muted-foreground">{t('noToolsInUse')}</p>
          )}
        </section>

        {/* Danger zone */}
        <section className="rounded-md border border-destructive/30 p-3 space-y-3">
          <h3 className="text-sm font-semibold text-destructive">{t('sectionDanger')}</h3>

          <div className="flex items-center justify-between gap-3">
            <div className="text-sm">
              <div className="font-medium">
                {isDisabled ? tCard('statusDisabled') : tCard('statusActive')}
              </div>
              {isDisabled && (
                <p className="text-xs text-muted-foreground">{t('disabledWarning')}</p>
              )}
            </div>
            <Button
              variant="outline"
              size="sm"
              onClick={() => handleStatusToggle(isDisabled)}
              disabled={updateConnection.isPending}
              aria-pressed={!isDisabled}
            >
              {isDisabled ? t('toggleToActive') : t('toggleToDisabled')}
            </Button>
          </div>

          <div className="space-y-1.5">
            <Button
              variant="destructive"
              size="sm"
              onClick={() => setDeleteOpen(true)}
              disabled={hasUsage || deleteConnection.isPending}
              className="w-full"
            >
              <Trash2Icon className="size-3.5" data-icon="inline-start" />
              {t('deleteButton')}
            </Button>
            {hasUsage ? (
              <p className="flex items-start gap-1.5 text-xs text-amber-700">
                <AlertTriangleIcon className="mt-0.5 size-3 shrink-0" />
                {t('deleteBlockedByUsage')}
              </p>
            ) : isOnlyDefaultPrebuilt ? (
              <p className="flex items-start gap-1.5 text-xs text-amber-700">
                <AlertTriangleIcon className="mt-0.5 size-3 shrink-0" />
                {t('defaultPrebuiltWarning')}
              </p>
            ) : (
              <p className="text-xs text-muted-foreground">{t('deleteButtonHint')}</p>
            )}
          </div>
        </section>
      </div>

      {/* Rebind credential — connection.credential_id 만 바뀌다, credential row는 그대로.
          drawer는 selected connection을 직접 update (default 여부와 무관) — connectionId
          명시로 PrebuiltBody가 standalone "연결 추가" 흐름이 아니라 단일 row patch를 수행. */}
      {rebindOpen && connection.type === 'prebuilt' && isPrebuiltProviderName(connection.provider_name) && (
        <ConnectionBindingDialog
          type="prebuilt"
          providerName={connection.provider_name}
          connectionId={connection.id}
          toolName={connection.display_name}
          open={rebindOpen}
          onOpenChange={setRebindOpen}
        />
      )}
      {rebindOpen && connection.type === 'custom' && (
        <ConnectionBindingDialog
          type="custom"
          currentConnectionId={connection.id}
          toolName={connection.display_name}
          open={rebindOpen}
          onOpenChange={setRebindOpen}
        />
      )}
      {/* MCP rebind은 server 단위 — server-group-card에서 수행. drawer에서는 안내만. */}

      <CredentialFormDialog
        open={credentialEditOpen}
        onOpenChange={setCredentialEditOpen}
        editingCredential={credential}
      />

      <DeleteConfirmDialog
        open={deleteOpen}
        onOpenChange={setDeleteOpen}
        title={t('deleteButton')}
        description={t('deleteButtonHint')}
        onConfirm={handleDelete}
        isPending={deleteConnection.isPending}
      />
    </>
  )
}
