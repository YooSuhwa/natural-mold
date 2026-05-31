'use client'

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
import { CredentialTestButton } from './credential-test-button'
import {
  useCredential,
  useCredentialAuditLogs,
  useDeleteCredential,
  useUpdateCredential,
} from '@/lib/hooks/use-credentials'
import { useStartOAuth2 } from '@/lib/hooks/use-credential-test'

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
  const { data: auditLogs } = useCredentialAuditLogs(credentialId, 10)
  const update = useUpdateCredential()
  const remove = useDeleteCredential()
  const oauth = useStartOAuth2()
  const [confirming, setConfirming] = useState(false)

  async function toggleStatus() {
    if (!credential) return
    const next = credential.status === 'disabled' ? 'active' : 'disabled'
    try {
      await update.mutateAsync({ id: credential.id, data: { status: next } })
      toast.success(t('toast.statusChanged', { status: next }))
    } catch (e) {
      toast.error(e instanceof Error ? e.message : t('toast.updateFailed'))
    }
  }

  async function handleDelete() {
    if (!credential) return
    try {
      await remove.mutateAsync(credential.id)
      toast.success(t('toast.deleted'))
      onOpenChange(false)
    } catch (e) {
      toast.error(e instanceof Error ? e.message : t('toast.deleteFailed'))
    }
  }

  async function handleOAuth() {
    if (!credential) return
    try {
      const { authorization_url } = await oauth.mutateAsync(credential.id)
      window.open(authorization_url, '_blank', 'noopener,noreferrer')
    } catch (e) {
      toast.error(e instanceof Error ? e.message : t('toast.oauthStartFailed'))
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
            actions={<StatusChip variant={credential.status} />}
          />
          <DialogShell.Body>
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

            <div className="space-y-2 text-xs">
              <Row label={t('rows.id')} value={credential.id} mono />
              <Row label={t('rows.keyId')} value={credential.key_id} mono />
              <Row label={t('rows.created')} value={new Date(credential.created_at).toLocaleString()} />
              <Row
                label={t('rows.lastUsed')}
                value={
                  credential.last_used_at
                    ? new Date(credential.last_used_at).toLocaleString()
                    : '—'
                }
              />
              <Row
                label={t('rows.lastTested')}
                value={
                  credential.last_tested_at
                    ? new Date(credential.last_tested_at).toLocaleString()
                    : '—'
                }
              />
              <Row
                label={t('rows.storedFields')}
                value={credential.field_keys.join(', ') || '—'}
                mono
              />
            </div>

            <section>
              <h3 className="mb-2 text-xs font-semibold uppercase tracking-wide text-muted-foreground">
                {t('audit.title')}
              </h3>
              {auditLogs && auditLogs.length > 0 ? (
                <ul className="space-y-1.5 text-xs">
                  {auditLogs.slice(0, 10).map((log) => (
                    <li
                      key={log.id}
                      className="flex items-start justify-between gap-2 rounded-md border border-border/60 bg-muted/30 px-2 py-1.5"
                    >
                      <div className="min-w-0">
                        <p className="font-medium">{log.action}</p>
                        <p className="text-[11px] text-muted-foreground">
                          {log.source}
                          {log.error ? ` · ${log.error}` : ''}
                        </p>
                      </div>
                      <span className="shrink-0 text-[11px] text-muted-foreground">
                        {new Date(log.created_at).toLocaleString()}
                      </span>
                    </li>
                  ))}
                </ul>
              ) : (
                <p className="text-xs text-muted-foreground">{t('audit.empty')}</p>
              )}
            </section>
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

function Row({ label, value, mono }: { label: string; value: string; mono?: boolean }) {
  return (
    <div className="flex items-start justify-between gap-2">
      <span className="text-muted-foreground">{label}</span>
      <span className={mono ? 'text-right font-mono text-[11px] break-all' : 'text-right'}>
        {value}
      </span>
    </div>
  )
}
