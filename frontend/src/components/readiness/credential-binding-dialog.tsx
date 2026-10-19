'use client'

import { useId, useMemo, useRef, useState } from 'react'
import { useTranslations } from 'next-intl'
import { KeyRoundIcon, PlusIcon } from 'lucide-react'
import { toast } from 'sonner'

import { CredentialCreateModal } from '@/components/credential/credential-create-modal'
import { DialogShell } from '@/components/shared/dialog-shell'
import { StatusChip } from '@/components/shared/status-chip'
import { Button } from '@/components/ui/button'
import { ApiError } from '@/lib/api/client'
import { useCredentials, useCredentialTypes } from '@/lib/hooks/use-credentials'
import {
  openOAuthPopup,
  useOAuthAuthorize,
  useOAuthCompletion,
} from '@/lib/hooks/use-oauth-completion'
import { useBindCredential } from '@/lib/hooks/use-readiness'
import type { Credential } from '@/lib/types/credential'
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

/**
 * S23 (plan 13.7): pick or create my credential of the required type and connect it. An
 * OAuth credential that still needs authorizing (new, `auth_needed`, `expired`) opens the
 * OAuth popup; the dialog reports "connected" once it is bound and the shared receiver
 * heard the completion (G-05).
 */
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
  const { data: definitions } = useCredentialTypes()
  const { authorize } = useOAuthAuthorize()
  const [creating, setCreating] = useState(false)
  const [awaitingOAuthId, setAwaitingOAuthId] = useState<string | null>(null)
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

  // One connect attempt: bound + (when OAuth) authorized → connected, in either order.
  const flowRef = useRef<{ bound: boolean; authorized: boolean } | null>(null)

  const finish = () => {
    flowRef.current = null
    setAwaitingOAuthId(null)
    toast.success(t('connected'))
    onOpenChange(false)
    onConnected?.()
  }

  useOAuthCompletion({
    credentialId: awaitingOAuthId,
    enabled: awaitingOAuthId !== null,
    onCompleted: () => {
      const flow = flowRef.current
      if (!flow) return
      flow.authorized = true
      if (flow.bound) finish()
    },
  })

  const needsAuthorization = (credential: Credential) =>
    credential.status !== 'active' &&
    (definitions ?? []).some((d) => d.key === credential.definition_key && d.has_oauth)

  /**
   * `oauth.popup` was opened in the click. The completion listener is on before the
   * binding is requested; a failed binding closes the popup and ends the attempt.
   */
  const connect = async (
    credentialId: string,
    oauth?: { popup: Window | null; started: boolean },
  ) => {
    const flow = { bound: false, authorized: !oauth }
    flowRef.current = flow
    if (oauth) setAwaitingOAuthId(credentialId)
    if (oauth && !oauth.started && (await authorize(credentialId, oauth.popup)) !== 'started') {
      flowRef.current = null
      setAwaitingOAuthId(null)
      toast.error(t('failed'))
      return
    }
    bind.mutate(
      { resourceType: target.resourceType, resourceId: target.resourceId, credentialId },
      {
        onSuccess: () => {
          flow.bound = true
          if (flow.authorized && flowRef.current === flow) finish()
        },
        onError: (error) => {
          oauth?.popup?.close()
          if (flowRef.current === flow) {
            flowRef.current = null
            setAwaitingOAuthId(null)
          }
          toast.error(t(errorKey(error)))
        },
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
          {awaitingOAuthId && (
            <p role="status" className="text-sm text-muted-foreground">
              {t('oauthWaiting')}
            </p>
          )}
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
                      onClick={() =>
                        void connect(
                          credential.id,
                          needsAuthorization(credential)
                            ? { popup: openOAuthPopup(), started: false }
                            : undefined,
                        )
                      }
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
          authorizeOAuth
          onCreated={(credentialId, options) => {
            setCreating(false)
            void connect(
              credentialId,
              options?.oauthStarted ? { popup: options.popup, started: true } : undefined,
            )
          }}
        />
      ) : null}
    </>
  )
}
