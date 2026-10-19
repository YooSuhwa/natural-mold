'use client'

import { PermissionsLoadError } from '@/components/shared/permissions-load-error'
import { useState } from 'react'
import { useTranslations } from 'next-intl'
import { toast } from 'sonner'
import { KeyRound, Power, Trash2 } from 'lucide-react'

import { Button } from '@/components/ui/button'
import { Skeleton } from '@/components/ui/skeleton'
import { StatusChip } from '@/components/shared/status-chip'
import { DomainIconTile } from '@/components/shared/icon'
import { DialogShell } from '@/components/shared/dialog-shell'
import { DeleteConfirmInline } from '@/components/shared/delete-confirm-inline'
import { ResourceShareButton } from '@/components/sharing/resource-share-button'
import { useApiErrorMessage } from '@/components/shared/api-error-state'
import { usePermissionCheck } from '@/lib/authz/use-permission'
import { useResourcePermissions } from '@/lib/hooks/use-resource-permissions'
import { CredentialAuditSection } from './credential-audit-section'
import { PersonalAccountBadge } from './personal-account-badge'
import { CredentialTestButton } from './credential-test-button'
import {
  useCredential,
  useDeleteCredential,
  useUpdateCredential,
} from '@/lib/hooks/use-credentials'
import { useStartOAuth2 } from '@/lib/hooks/use-credential-test'
import { openExternalUrl } from '@/lib/browser/window-open'
import { formatDisplayDateTime } from '@/lib/utils/display-format'

interface Props {
  credentialId: string | null
  open: boolean
  onOpenChange: (open: boolean) => void
}

export function CredentialDetailDialog(props: Props) {
  // Re-mount inner when credentialId changes so internal state (confirming, etc.) resets cleanly.
  return <CredentialDetailDialogInner key={props.credentialId ?? 'closed'} {...props} />
}

function CredentialDetailDialogInner({ credentialId, open, onOpenChange }: Props) {
  const t = useTranslations('credentials.detail')
  const tc = useTranslations('common')
  const { data: credential, isLoading } = useCredential(credentialId)
  const permissions = useResourcePermissions('credential', [credentialId])
  const { permissionsOf } = permissions
  const can = usePermissionCheck()
  const errorMessage = useApiErrorMessage()
  const update = useUpdateCredential()
  const remove = useDeleteCredential()
  const oauth = useStartOAuth2()
  const [confirming, setConfirming] = useState(false)
  // Plan 13.9: a credential shared with me shows its metadata only (no test/edit/delete).
  const source = {
    myPermissions: permissionsOf(credential?.id),
    ownerId: credential?.user_id ?? null,
    isSystem: credential?.is_system,
  }
  const canEdit = !!credential && can(source, 'can_edit')
  const canDelete = !!credential && can(source, 'can_delete')

  async function toggleStatus() {
    if (!credential) return
    const next = credential.status === 'disabled' ? 'active' : 'disabled'
    try {
      await update.mutateAsync({ id: credential.id, data: { status: next } })
      toast.success(t('toast.statusChanged', { status: next }))
    } catch (e) {
      toast.error(errorMessage(e, t('toast.updateFailed')))
    }
  }

  async function handleDelete() {
    if (!credential) return
    try {
      await remove.mutateAsync(credential.id)
      toast.success(t('toast.deleted'))
      onOpenChange(false)
    } catch (e) {
      toast.error(errorMessage(e, t('toast.deleteFailed')))
    }
  }

  async function handleOAuth() {
    if (!credential) return
    try {
      const { authorization_url } = await oauth.mutateAsync(credential.id)
      if (!openExternalUrl(authorization_url)) {
        toast.error(t('toast.oauthStartFailed'))
      }
    } catch (e) {
      toast.error(errorMessage(e, t('toast.oauthStartFailed')))
    }
  }

  return (
    <DialogShell open={open} onOpenChange={onOpenChange} size="lg">
      {isLoading || !credential ? (
        <>
          <DialogShell.Header title={t('loading')} />
          <DialogShell.Body>
            <Skeleton className="h-32 w-full rounded-lg" />
            <Skeleton className="h-40 w-full rounded-lg" />
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
                iconId={credential.definition_key}
                fallback="credential"
                className="size-9"
                iconClassName="size-5"
              />
            }
            title={credential.name}
            description={credential.definition_key}
            actions={
              <>
                <PersonalAccountBadge personal={credential.personal} />
                <StatusChip variant={credential.status} />
              </>
            }
          />
          <DialogShell.Body>
            <PermissionsLoadError
              error={permissions.error}
              onRetry={() => void permissions.refetch()}
            />
            {canEdit ? (
              <div className="flex flex-wrap gap-2">
                <CredentialTestButton credentialId={credential.id} />
                <Button variant="outline" size="sm" onClick={toggleStatus}>
                  <Power className="size-3.5" />
                  {credential.status === 'disabled' ? t('enable') : t('disable')}
                </Button>
                <Button variant="outline" size="sm" onClick={handleOAuth}>
                  <KeyRound className="size-3.5" />
                  {t('reauthorize')}
                </Button>
              </div>
            ) : (
              <p className="text-xs text-muted-foreground">{t('sharedMetadataOnly')}</p>
            )}

            <div className="space-y-2 text-xs">
              <Row label={t('rows.id')} value={credential.id} mono />
              <Row label={t('rows.keyId')} value={credential.key_id} mono />
              <Row label={t('rows.created')} value={formatDisplayDateTime(credential.created_at)} />
              <Row
                label={t('rows.lastUsed')}
                value={
                  credential.last_used_at ? formatDisplayDateTime(credential.last_used_at) : '—'
                }
              />
              <Row
                label={t('rows.lastTested')}
                value={
                  credential.last_tested_at ? formatDisplayDateTime(credential.last_tested_at) : '—'
                }
              />
              <Row
                label={t('rows.storedFields')}
                value={credential.field_keys.join(', ') || '—'}
                mono
              />
            </div>

            {canEdit ? <CredentialAuditSection credentialId={credential.id} /> : null}
          </DialogShell.Body>
          <DialogShell.Footer>
            {!canDelete ? (
              <span className="mr-auto" />
            ) : confirming ? (
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
            {credential.is_system || credential.personal ? null : (
              <ResourceShareButton
                resourceType="credential"
                resourceId={credential.id}
                resourceName={credential.name}
                ownerId={credential.user_id}
                myPermissions={permissionsOf(credential.id)}
              />
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

function Row({ label, value, mono }: { label: string; value: string; mono?: boolean }) {
  return (
    <div className="flex items-start justify-between gap-2">
      <span className="text-muted-foreground">{label}</span>
      <span className={mono ? 'text-right font-mono moldy-ui-caption break-all' : 'text-right'}>
        {value}
      </span>
    </div>
  )
}
