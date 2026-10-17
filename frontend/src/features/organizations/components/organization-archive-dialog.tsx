'use client'

import { useId, useState } from 'react'
import { useTranslations } from 'next-intl'
import { toast } from 'sonner'

import { DialogShell } from '@/components/shared/dialog-shell'
import { FormFieldShell } from '@/components/shared/form-field-shell'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { useArchiveTenantOrganization } from '@/lib/hooks/use-organizations'
import type { TenantOrganizationSummary } from '@/lib/types/organization'
import { useOrgErrorMessage } from '../lib/error-message'

interface OrganizationArchiveActionProps {
  tenantId: string
  organization: TenantOrganizationSummary
}

/**
 * Company-admin archive entry for one organization (13.12.2 "조직 보관").
 * Policy A: a default organization is never archivable, so nothing is offered for it;
 * archived or suspended organizations are not archivable either.
 */
export function OrganizationArchiveAction({
  tenantId,
  organization,
}: OrganizationArchiveActionProps) {
  const t = useTranslations('orgAdmin.archiveDialog')
  const [open, setOpen] = useState(false)
  if (organization.is_default || organization.status !== 'active') return null
  return (
    <>
      <Button
        size="sm"
        variant="outline"
        aria-label={t('openFor', { name: organization.name })}
        onClick={() => setOpen(true)}
      >
        {t('open')}
      </Button>
      <DialogShell open={open} onOpenChange={setOpen} size="sm" height="auto">
        <DialogShell.Header
          title={t('title', { name: organization.name })}
          description={t('description')}
        />
        {open ? (
          <ArchiveBody
            tenantId={tenantId}
            organization={organization}
            onClose={() => setOpen(false)}
          />
        ) : null}
      </DialogShell>
    </>
  )
}

function ArchiveBody({
  tenantId,
  organization,
  onClose,
}: OrganizationArchiveActionProps & { onClose: () => void }) {
  const t = useTranslations('orgAdmin.archiveDialog')
  const errorMessage = useOrgErrorMessage()
  const archive = useArchiveTenantOrganization(tenantId)
  const inputId = useId()
  const [confirmName, setConfirmName] = useState('')
  const [error, setError] = useState<string | null>(null)
  const matches = confirmName === organization.name

  function confirm() {
    setError(null)
    archive.mutate(
      {
        orgId: organization.id,
        data: { confirm_name: confirmName },
        updatedAt: organization.updated_at,
      },
      {
        onSuccess: () => {
          toast.success(t('done', { name: organization.name }))
          onClose()
        },
        onError: (err) => setError(errorMessage(err)),
      },
    )
  }

  return (
    <>
      <DialogShell.Body>
        <div className="space-y-3">
          <ul className="list-disc space-y-1 pl-5 text-sm">
            <li>{t('members', { count: organization.member_count })}</li>
            <li>{t('runs')}</li>
            <li>{t('schedules')}</li>
            <li>{t('restore')}</li>
          </ul>
          <FormFieldShell
            id={inputId}
            label={t('confirmLabel', { name: organization.name })}
            error={error}
          >
            <Input
              id={inputId}
              value={confirmName}
              autoComplete="off"
              onChange={(event) => setConfirmName(event.target.value)}
            />
          </FormFieldShell>
        </div>
      </DialogShell.Body>
      <DialogShell.Footer>
        <Button variant="outline" autoFocus onClick={onClose}>
          {t('cancel')}
        </Button>
        <Button variant="destructive" disabled={archive.isPending || !matches} onClick={confirm}>
          {t('submit')}
        </Button>
      </DialogShell.Footer>
    </>
  )
}
