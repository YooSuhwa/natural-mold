'use client'

import { useState } from 'react'
import { useTranslations } from 'next-intl'
import { toast } from 'sonner'
import { Trash2 } from 'lucide-react'

import { Button } from '@/components/ui/button'
import { Skeleton } from '@/components/ui/skeleton'
import { Badge } from '@/components/ui/badge'
import { DomainIconTile } from '@/components/shared/icon'
import { DialogShell } from '@/components/shared/dialog-shell'
import { DeleteConfirmInline } from '@/components/shared/delete-confirm-inline'
import { CredentialPicker } from '@/components/credential/credential-picker'
import { ToolRunPanel } from './tool-run-panel'
import { useDeleteTool, useTool, useToolType, useUpdateTool } from '@/lib/hooks/use-tools'

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
  const { data: tool, isLoading } = useTool(toolId)
  const { data: definition } = useToolType(tool?.definition_key)
  const update = useUpdateTool()
  const remove = useDeleteTool()
  const [confirming, setConfirming] = useState(false)

  async function handleCredentialChange(next: string | null) {
    if (!tool) return
    try {
      await update.mutateAsync({ id: tool.id, data: { credential_id: next } })
      toast.success(t('toast.credentialUpdated'))
    } catch (e) {
      toast.error(e instanceof Error ? e.message : t('toast.updateFailed'))
    }
  }

  async function handleDelete() {
    if (!tool) return
    try {
      await remove.mutateAsync(tool.id)
      toast.success(t('toast.deleted'))
      onOpenChange(false)
    } catch (e) {
      toast.error(e instanceof Error ? e.message : t('toast.deleteFailed'))
    }
  }

  return (
    <DialogShell open={open} onOpenChange={onOpenChange} size="lg">
      {isLoading || !tool ? (
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
                <span>{tool.description}</span>
              </span>
            }
          />
          <DialogShell.Body>
            {definition?.credential_definition_keys.length ? (
              <div className="space-y-1.5">
                <label className="text-xs font-medium">{t('credential')}</label>
                <CredentialPicker
                  value={tool.credential_id}
                  onChange={handleCredentialChange}
                  definitionKeys={definition.credential_definition_keys}
                />
              </div>
            ) : null}

            <ToolRunPanel toolId={tool.id} />
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
            ) : (
              <Button
                variant="ghost"
                size="sm"
                className="mr-auto text-destructive hover:bg-destructive/10 hover:text-destructive"
                onClick={() => setConfirming(true)}
              >
                <Trash2 className="size-3.5" />
                {t('delete')}
              </Button>
            )}
            <Button variant="outline" onClick={() => onOpenChange(false)}>
              {tc('close')}
            </Button>
          </DialogShell.Footer>
        </>
      )}
    </DialogShell>
  )
}
