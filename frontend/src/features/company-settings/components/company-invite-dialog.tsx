'use client'

import { useState } from 'react'
import { useTranslations } from 'next-intl'
import { toast } from 'sonner'

import { DialogShell } from '@/components/shared/dialog-shell'
import { FormFieldShell } from '@/components/shared/form-field-shell'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { useOrgErrorMessage } from '@/features/organizations/lib/error-message'
import { useTenantInvitationMutations } from '@/lib/hooks/use-tenant-admin'
import type { TenantInvitationCreated, TenantRole } from '@/lib/types/tenant-admin'
import { formatDisplayDateTime } from '@/lib/utils/display-format'
import type { CompanyScope } from './company-admin-gate'

type InviteRole = TenantRole | 'none'
const INVITE_ROLES: readonly InviteRole[] = ['none', 'admin', 'auditor']

export function CompanyInviteDialog({
  scope,
  onClose,
}: {
  scope: CompanyScope
  onClose: () => void
}) {
  const t = useTranslations('orgAdmin.companySettings.invitations')
  const tCommon = useTranslations('orgAdmin.common')
  const errorMessage = useOrgErrorMessage()
  const { create } = useTenantInvitationMutations(scope.tenantId)
  const [email, setEmail] = useState('')
  const [role, setRole] = useState<InviteRole>('none')
  const [error, setError] = useState<string | null>(null)
  const [created, setCreated] = useState<TenantInvitationCreated | null>(null)
  const trimmed = email.trim()

  if (created) return <InvitationTokenResult invitation={created} onClose={onClose} />

  return (
    <DialogShell
      open
      onOpenChange={(open) => (open ? undefined : onClose())}
      size="sm"
      height="auto"
    >
      <DialogShell.Header title={t('inviteTitle')} description={t('inviteDescription')} />
      <DialogShell.Body>
        <div className="space-y-3">
          <FormFieldShell id="company-invite-email" label={t('email')} required error={error}>
            <Input
              id="company-invite-email"
              type="email"
              value={email}
              onChange={(event) => setEmail(event.target.value)}
            />
          </FormFieldShell>
          <fieldset className="space-y-2">
            <legend className="moldy-ui-label text-foreground">{t('roleLabel')}</legend>
            <div className="flex flex-wrap gap-2">
              {INVITE_ROLES.map((option) => (
                <Button
                  key={option}
                  size="sm"
                  variant={role === option ? 'default' : 'outline'}
                  aria-pressed={role === option}
                  onClick={() => setRole(option)}
                >
                  {t(`role.${option}`)}
                </Button>
              ))}
            </div>
          </fieldset>
        </div>
      </DialogShell.Body>
      <DialogShell.Footer>
        <Button variant="outline" onClick={onClose}>
          {tCommon('cancel')}
        </Button>
        <Button
          disabled={trimmed.length === 0 || create.isPending}
          onClick={() => {
            setError(null)
            create.mutate(
              {
                data: { email: trimmed, roles: role === 'none' ? [] : [role] },
                version: scope.overview.updated_at,
              },
              { onSuccess: setCreated, onError: (err) => setError(errorMessage(err)) },
            )
          }}
        >
          {t('submit')}
        </Button>
      </DialogShell.Footer>
    </DialogShell>
  )
}

async function copyToClipboard(text: string): Promise<boolean> {
  try {
    if (!navigator.clipboard?.writeText) return false
    await navigator.clipboard.writeText(text)
    return true
  } catch {
    return false
  }
}

/** The invitation token is a one-time secret: clipboard only, never rendered or logged. */
export function InvitationTokenResult({
  invitation,
  onClose,
}: {
  invitation: TenantInvitationCreated
  onClose: () => void
}) {
  const t = useTranslations('orgAdmin.companySettings.invitations')
  const tCommon = useTranslations('orgAdmin.common')
  const token = invitation.token
  return (
    <DialogShell
      open
      onOpenChange={(open) => (open ? undefined : onClose())}
      size="sm"
      height="auto"
    >
      <DialogShell.Header
        title={t('createdTitle')}
        description={t('createdDescription', { email: invitation.email })}
      />
      <DialogShell.Body>
        <div className="space-y-2">
          <p className="text-xs text-muted-foreground">
            {t('expires', { date: formatDisplayDateTime(invitation.expires_at) })}
          </p>
          {token ? (
            <>
              <p className="text-xs text-muted-foreground">{t('tokenOnce')}</p>
              <Button
                variant="outline"
                size="sm"
                onClick={() => {
                  void copyToClipboard(token).then((copied) =>
                    copied ? toast.success(t('tokenCopied')) : toast.error(t('tokenCopyFailed')),
                  )
                }}
              >
                {t('copyToken')}
              </Button>
            </>
          ) : (
            <p className="text-xs text-muted-foreground">{t('tokenUnavailable')}</p>
          )}
        </div>
      </DialogShell.Body>
      <DialogShell.Footer>
        <Button onClick={onClose}>{tCommon('close')}</Button>
      </DialogShell.Footer>
    </DialogShell>
  )
}
