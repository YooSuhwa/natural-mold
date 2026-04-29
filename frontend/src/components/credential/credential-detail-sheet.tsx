'use client'

import { useState } from 'react'
import { toast } from 'sonner'
import { Trash2, Power, KeyRound } from 'lucide-react'

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
import { DomainIcon } from '@/components/shared/icon'
import { CredentialTestButton } from './credential-test-button'
import { useCredential, useDeleteCredential, useUpdateCredential, useCredentialAuditLogs } from '@/lib/hooks/use-credentials'
import { useStartOAuth2 } from '@/lib/hooks/use-credential-test'

interface CredentialDetailSheetProps {
  credentialId: string | null
  open: boolean
  onOpenChange: (open: boolean) => void
}

export function CredentialDetailSheet({
  credentialId,
  open,
  onOpenChange,
}: CredentialDetailSheetProps) {
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
      toast.success(`Credential ${next}`)
    } catch (e) {
      toast.error(e instanceof Error ? e.message : 'Update failed')
    }
  }

  async function handleDelete() {
    if (!credential) return
    try {
      await remove.mutateAsync(credential.id)
      toast.success('Credential deleted')
      onOpenChange(false)
    } catch (e) {
      toast.error(e instanceof Error ? e.message : 'Delete failed')
    }
  }

  async function handleOAuth() {
    if (!credential) return
    try {
      const { authorization_url } = await oauth.mutateAsync(credential.id)
      window.open(authorization_url, '_blank', 'noopener,noreferrer')
    } catch (e) {
      toast.error(e instanceof Error ? e.message : 'OAuth start failed')
    }
  }

  return (
    <Sheet open={open} onOpenChange={onOpenChange}>
      <SheetContent className="w-full sm:max-w-md flex flex-col gap-4 overflow-y-auto p-0">
        <SheetHeader className="border-b">
          {isLoading || !credential ? (
            <SheetTitle>Loading...</SheetTitle>
          ) : (
            <>
              <div className="flex items-center gap-3">
                <DomainIcon iconId={credential.definition_key} className="size-5" />
                <SheetTitle>{credential.name}</SheetTitle>
              </div>
              <SheetDescription className="flex items-center gap-2">
                <span className="text-xs">{credential.definition_key}</span>
                <StatusChip variant={credential.status} />
              </SheetDescription>
            </>
          )}
        </SheetHeader>

        {credential && (
          <div className="flex-1 px-4 pb-4 space-y-4">
            <div className="flex flex-wrap gap-2">
              <CredentialTestButton credentialId={credential.id} />
              <Button variant="outline" size="sm" onClick={toggleStatus}>
                <Power className="size-3.5" />
                {credential.status === 'disabled' ? 'Enable' : 'Disable'}
              </Button>
              <Button variant="outline" size="sm" onClick={handleOAuth}>
                <KeyRound className="size-3.5" />
                Re-authorize
              </Button>
              <Button
                variant="outline"
                size="sm"
                onClick={() => setConfirming(true)}
                className="text-destructive hover:text-destructive"
              >
                <Trash2 className="size-3.5" />
                Delete
              </Button>
            </div>

            <Separator />

            <div className="space-y-2 text-xs">
              <Row label="ID" value={credential.id} mono />
              <Row label="Key ID" value={credential.key_id} mono />
              <Row
                label="Created"
                value={new Date(credential.created_at).toLocaleString()}
              />
              <Row
                label="Last used"
                value={
                  credential.last_used_at
                    ? new Date(credential.last_used_at).toLocaleString()
                    : '—'
                }
              />
              <Row
                label="Last tested"
                value={
                  credential.last_tested_at
                    ? new Date(credential.last_tested_at).toLocaleString()
                    : '—'
                }
              />
              <Row
                label="Stored fields"
                value={credential.field_keys.join(', ') || '—'}
                mono
              />
            </div>

            <Separator />

            <div className="space-y-2">
              <h3 className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">
                Recent audit log
              </h3>
              {auditLogs && auditLogs.length > 0 ? (
                <ul className="space-y-1.5 text-xs">
                  {auditLogs.slice(0, 10).map((log) => (
                    <li
                      key={log.id}
                      className="flex items-start justify-between gap-2 rounded border bg-muted/30 px-2 py-1.5"
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
                <p className="text-xs text-muted-foreground">No audit entries yet.</p>
              )}
            </div>

            {confirming && (
              <div className="rounded border border-destructive/40 bg-destructive/5 p-3 text-xs">
                <p className="font-medium text-destructive">Delete this credential?</p>
                <p className="mt-1 text-muted-foreground">
                  Tools using it will become unauthenticated.
                </p>
                <div className="mt-2 flex gap-2">
                  <Button
                    size="sm"
                    variant="outline"
                    onClick={() => setConfirming(false)}
                  >
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

function Row({ label, value, mono }: { label: string; value: string; mono?: boolean }) {
  return (
    <div className="flex items-start justify-between gap-2">
      <span className="text-muted-foreground">{label}</span>
      <span className={mono ? 'font-mono text-[11px] break-all text-right' : 'text-right'}>
        {value}
      </span>
    </div>
  )
}
