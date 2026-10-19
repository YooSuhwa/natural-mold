'use client'

import { PermissionsLoadError } from '@/components/shared/permissions-load-error'
import { useMemo, useState } from 'react'
import { useTranslations } from 'next-intl'
import { toast } from 'sonner'
import { Trash2 } from 'lucide-react'

import { Button } from '@/components/ui/button'
import { Skeleton } from '@/components/ui/skeleton'
import { Badge } from '@/components/ui/badge'
import { DomainIconTile } from '@/components/shared/icon'
import { DialogShell } from '@/components/shared/dialog-shell'
import { DeleteConfirmInline } from '@/components/shared/delete-confirm-inline'
import { ApiErrorState, useApiErrorMessage } from '@/components/shared/api-error-state'
import { CredentialPicker } from '@/components/credential/credential-picker'
import { ResourceShareButton, SharedWithMeBadge } from '@/components/sharing/resource-share-button'
import { ToolRunPanel } from './tool-run-panel'
import { useDeleteTool, useTool, useToolType, useUpdateTool } from '@/lib/hooks/use-tools'
import { useResourceAccess } from '@/lib/authz/use-resource-access'

interface Props {
  toolId: string | null
  open: boolean
  onOpenChange: (open: boolean) => void
}

export function ToolDetailDialog(props: Props) {
  return <ToolDetailDialogInner key={props.toolId ?? 'closed'} {...props} />
}

function ToolDetailDialogInner({ toolId, open, onOpenChange }: Props) {
  const t = useTranslations('tool.detailDialog')
  const tc = useTranslations('common')
  const { data: tool, isLoading, error } = useTool(toolId)
  const { data: definition } = useToolType(tool?.definition_key)
  const update = useUpdateTool()
  const remove = useDeleteTool()
  const [confirming, setConfirming] = useState(false)
  const errorMessage = useApiErrorMessage()
  const rows = useMemo(() => (tool ? [tool] : []), [tool])
  const access = useResourceAccess('tool', rows)
  // Plan 13.9: test runs and settings need can_edit, deletion can_delete.
  const canEdit = access.can(tool, 'can_edit')
  const canDelete = access.can(tool, 'can_delete')

  async function handleCredentialChange(next: string | null) {
    if (!tool) return
    try {
      await update.mutateAsync({ id: tool.id, data: { credential_id: next } })
      toast.success(t('toast.credentialUpdated'))
    } catch (e) {
      toast.error(errorMessage(e, e instanceof Error ? e.message : t('toast.updateFailed')))
    }
  }

  async function handleDelete() {
    if (!tool) return
    try {
      await remove.mutateAsync(tool.id)
      toast.success(t('toast.deleted'))
      onOpenChange(false)
    } catch (e) {
      toast.error(errorMessage(e, e instanceof Error ? e.message : t('toast.deleteFailed')))
    }
  }

  return (
    <DialogShell open={open} onOpenChange={onOpenChange} size="lg">
      {error && toolId ? (
        <>
          <DialogShell.Header title={t('loading')} />
          <DialogShell.Body>
            <ApiErrorState error={error} resource={{ type: 'tool', id: toolId }} />
          </DialogShell.Body>
        </>
      ) : isLoading || !tool ? (
        <>
          <DialogShell.Header title={t('loading')} />
          <DialogShell.Body>
            <Skeleton className="h-32 w-full rounded-lg" />
          </DialogShell.Body>
          <DialogShell.Footer>
            <Button variant="outline" onClick={() => onOpenChange(false)}>
              {tc('close')}
            </Button>
          </DialogShell.Footer>
        </>
      ) : (
        <>
          <DialogShell.Header
            icon={
              <DomainIconTile
                iconId={definition?.icon_id ?? tool.definition_key}
                fallback="tool"
                className="size-9"
                iconClassName="size-5"
              />
            }
            title={tool.name}
            description={
              <span className="inline-flex items-center gap-2">
                <Badge variant="secondary">{tool.definition_key}</Badge>
                <SharedWithMeBadge ownerId={tool.user_id} />
                <span>{tool.description}</span>
              </span>
            }
          />
          <DialogShell.Body>
            <PermissionsLoadError error={access.error} onRetry={() => void access.refetch()} />
            {definition?.credential_definition_keys.length ? (
              <div className="space-y-1.5">
                <label className="text-xs font-medium">{t('credential')}</label>
                <CredentialPicker
                  disabled={!canEdit}
                  value={tool.credential_id}
                  onChange={handleCredentialChange}
                  definitionKeys={definition.credential_definition_keys}
                />
              </div>
            ) : null}

            {canEdit ? (
              <ToolRunPanel toolId={tool.id} />
            ) : (
              <p className="text-xs text-muted-foreground">{tc('readOnly')}</p>
            )}
          </DialogShell.Body>
          <DialogShell.Footer>
            {confirming ? (
              <div className="flex-1">
                <DeleteConfirmInline
                  entity={t('entity')}
                  onCancel={() => setConfirming(false)}
                  onConfirm={handleDelete}
                  pending={remove.isPending}
                />
              </div>
            ) : canDelete ? (
              <Button
                variant="ghost"
                size="sm"
                className="mr-auto text-destructive hover:bg-destructive/10 hover:text-destructive"
                onClick={() => setConfirming(true)}
              >
                <Trash2 className="size-3.5" />
                {t('delete')}
              </Button>
            ) : (
              <span className="mr-auto" />
            )}
            <ResourceShareButton
              resourceType="tool"
              resourceId={tool.id}
              resourceName={tool.name}
              ownerId={tool.user_id}
              myPermissions={access.permissionsOf(tool.id)}
              sharedAccount={!!tool.credential_id}
            />
            <Button variant="outline" onClick={() => onOpenChange(false)}>
              {tc('close')}
            </Button>
          </DialogShell.Footer>
        </>
      )}
    </DialogShell>
  )
}
