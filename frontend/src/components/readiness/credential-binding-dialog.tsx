'use client'

import { useId, useMemo, useState } from 'react'
import { useTranslations } from 'next-intl'
import { KeyRoundIcon, PlusIcon } from 'lucide-react'
import { toast } from 'sonner'

import { CredentialCreateModal } from '@/components/credential/credential-create-modal'
import { DialogShell } from '@/components/shared/dialog-shell'
import { StatusChip } from '@/components/shared/status-chip'
import { Button } from '@/components/ui/button'
import { ApiError } from '@/lib/api/client'
import { useCredentials } from '@/lib/hooks/use-credentials'
import { useBindCredential } from '@/lib/hooks/use-readiness'
import type { BindableResourceType } from '@/lib/types/readiness'

export interface CredentialBindingTarget {
  readonly resourceType: BindableResourceType
  readonly resourceId: string
  readonly resourceName: string
  /** The type the resource accepts (readiness `credential.definition_key`); unknown → all. */
  readonly definitionKey?: string | null
}

interface CredentialBindingDialogProps {
  readonly open: boolean
  readonly onOpenChange: (open: boolean) => void
  readonly target: CredentialBindingTarget
  readonly agentName?: string | null
  readonly onConnected?: () => void
}

function errorKey(error: unknown): 'typeMismatch' | 'notPerUser' | 'failed' {
  if (!(error instanceof ApiError)) return 'failed'
  if (error.code === 'credential_type_mismatch') return 'typeMismatch'
  if (error.code === 'credential_mode_not_per_user') return 'notPerUser'
  return 'failed'
}

/** S23 (plan 13.7): pick or create my credential of the required type and connect it. */
export function CredentialBindingDialog({
  open,
  onOpenChange,
  target,
  agentName,
  onConnected,
}: CredentialBindingDialogProps) {
  const t = useTranslations('readiness.binding')
  const { data: credentials } = useCredentials()
  const bind = useBindCredential()
  const [creating, setCreating] = useState(false)
  const mineId = useId()
  const { definitionKey } = target

  const candidates = useMemo(
    () =>
      (credentials ?? []).filter(
        (credential) =>
          !credential.is_system && (!definitionKey || credential.definition_key === definitionKey),
      ),
    [credentials, definitionKey],
  )

  const connect = (credentialId: string) => {
    bind.mutate(
      { resourceType: target.resourceType, resourceId: target.resourceId, credentialId },
      {
        onSuccess: () => {
          toast.success(t('connected'))
          onOpenChange(false)
          onConnected?.()
        },
        onError: (error) => toast.error(t(errorKey(error))),
      },
    )
  }

  return (
    <>
      <DialogShell open={open} onOpenChange={onOpenChange} size="sm" height="auto">
        <DialogShell.Header
          icon={<KeyRoundIcon className="size-5" />}
          title={t('title', { name: target.resourceName })}
          description={agentName ? t('description', { agent: agentName }) : t('descriptionGeneric')}
        />
        <DialogShell.Body className="space-y-4">
          <section aria-labelledby={mineId} className="space-y-2">
            <h3 id={mineId} className="text-xs font-medium text-muted-foreground">
              {t('mine')}
            </h3>
            {candidates.length === 0 ? (
              <p className="text-sm text-muted-foreground">{t('empty')}</p>
            ) : (
              <ul className="space-y-2">
                {candidates.map((credential) => (
                  <li
                    key={credential.id}
                    className="flex items-center gap-3 rounded-md border border-border px-3 py-2"
                  >
                    <div className="min-w-0 flex-1">
                      <p className="truncate text-sm font-medium">{credential.name}</p>
                      <StatusChip variant={credential.status} className="mt-1" />
                    </div>
                    <Button
                      size="sm"
                      disabled={bind.isPending}
                      onClick={() => connect(credential.id)}
                      aria-label={t('useNamed', { name: credential.name })}
                    >
                      {t('use')}
                    </Button>
                  </li>
                ))}
              </ul>
            )}
          </section>
          <div className="flex items-center gap-3 rounded-md border border-dashed border-border px-3 py-2">
            <p className="min-w-0 flex-1 text-xs text-muted-foreground">{t('createHelp')}</p>
            <Button
              size="sm"
              variant="outline"
              disabled={bind.isPending}
              onClick={() => setCreating(true)}
            >
              <PlusIcon className="size-3.5" />
              {t('create')}
            </Button>
          </div>
        </DialogShell.Body>
        <DialogShell.Footer>
          <Button variant="outline" onClick={() => onOpenChange(false)}>
            {t('close')}
          </Button>
        </DialogShell.Footer>
      </DialogShell>
      {creating ? (
        <CredentialCreateModal
          open={creating}
          onOpenChange={setCreating}
          presetDefinitionKey={definitionKey ?? undefined}
          initialName={target.resourceName}
          onCreated={(credentialId) => {
            setCreating(false)
            connect(credentialId)
          }}
        />
      ) : null}
    </>
  )
}
