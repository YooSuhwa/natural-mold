'use client'

import { useState } from 'react'
import { useTranslations } from 'next-intl'
import { toast } from 'sonner'

import { DialogShell } from '@/components/shared/dialog-shell'
import { FormFieldShell } from '@/components/shared/form-field-shell'
import { SettingsSectionCard } from '@/components/shared/settings-section-card'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Skeleton } from '@/components/ui/skeleton'
import {
  useArchiveTenantOrganization,
  useOrganizationInfo,
  useRestoreTenantOrganization,
  useUpdateOrganization,
} from '@/lib/hooks/use-organizations'
import type { OrganizationSummary, OrganizationView } from '@/lib/types/organization'
import { orgAccess } from '../lib/access'
import { useOrgErrorMessage } from '../lib/error-message'
import { OrgPageFrame } from './org-page-frame'

export function OrganizationSettingsContent() {
  const t = useTranslations('orgAdmin.settings')
  return (
    <OrgPageFrame title={t('title')} description={t('description')}>
      {(organization) => <OrganizationSettingsBody organization={organization} />}
    </OrgPageFrame>
  )
}

function OrganizationSettingsBody({ organization }: { organization: OrganizationSummary }) {
  const t = useTranslations('orgAdmin.settings')
  const errorMessage = useOrgErrorMessage()
  const info = useOrganizationInfo(organization.id)
  // Lives above the version-keyed form so a stale_version message survives the
  // refetch-driven remount that reseeds the form with the latest name.
  const [renameError, setRenameError] = useState<string | null>(null)

  if (info.isLoading) return <Skeleton className="h-40 w-full" />
  if (!info.data) {
    return <p className="text-sm text-status-danger">{errorMessage(info.error)}</p>
  }
  const view = info.data
  const access = orgAccess(organization, view.permissions)

  return (
    <div className="space-y-4">
      <RenameSection
        key={view.updated_at}
        view={view}
        canEdit={access.canManagePolicy}
        error={renameError}
        onError={setRenameError}
      />
      {access.canManagePolicy ? (
        <SettingsSectionCard title={t('lifecycle.title')} description={t('lifecycle.description')}>
          {view.status === 'archived' ? (
            <RestoreAction view={view} />
          ) : (
            <ArchiveAction view={view} />
          )}
        </SettingsSectionCard>
      ) : null}
    </div>
  )
}

function RenameSection({
  view,
  canEdit,
  error,
  onError,
}: {
  view: OrganizationView
  canEdit: boolean
  error: string | null
  onError: (message: string | null) => void
}) {
  const t = useTranslations('orgAdmin.settings')
  const errorMessage = useOrgErrorMessage()
  const update = useUpdateOrganization(view.id)
  const [name, setName] = useState(view.name)
  const trimmed = name.trim()

  function submit() {
    onError(null)
    update.mutate(
      { data: { name: trimmed }, version: view.updated_at },
      {
        onSuccess: () => toast.success(t('rename.saved')),
        onError: (err) => onError(errorMessage(err)),
      },
    )
  }

  return (
    <SettingsSectionCard title={t('rename.title')} description={t('rename.description')}>
      <div className="space-y-3">
        <FormFieldShell id="org-name" label={t('rename.label')} error={error}>
          <Input
            id="org-name"
            value={name}
            maxLength={200}
            disabled={!canEdit}
            onChange={(event) => setName(event.target.value)}
          />
        </FormFieldShell>
        <p className="text-xs text-muted-foreground">{t('rename.slug', { slug: view.slug })}</p>
        {canEdit ? (
          <Button
            onClick={submit}
            disabled={update.isPending || trimmed.length === 0 || trimmed === view.name}
          >
            {t('rename.submit')}
          </Button>
        ) : (
          <p className="text-xs text-muted-foreground">{t('readOnly')}</p>
        )}
      </div>
    </SettingsSectionCard>
  )
}

function ArchiveAction({ view }: { view: OrganizationView }) {
  const t = useTranslations('orgAdmin.settings.archive')
  const tCommon = useTranslations('orgAdmin.common')
  const errorMessage = useOrgErrorMessage()
  const archive = useArchiveTenantOrganization(view.tenant_id)
  const [open, setOpen] = useState(false)
  const [confirmName, setConfirmName] = useState('')
  const [error, setError] = useState<string | null>(null)

  function close(nextOpen: boolean) {
    setOpen(nextOpen)
    if (!nextOpen) {
      setConfirmName('')
      setError(null)
    }
  }

  function confirm() {
    setError(null)
    archive.mutate(
      { orgId: view.id, data: { confirm_name: confirmName }, updatedAt: view.updated_at },
      {
        onSuccess: () => {
          toast.success(t('done'))
          close(false)
        },
        onError: (err) => setError(errorMessage(err)),
      },
    )
  }

  return (
    <div className="space-y-2">
      <p className="text-sm text-muted-foreground">
        {view.is_default ? t('defaultForbidden') : t('description')}
      </p>
      <Button variant="destructive" disabled={view.is_default} onClick={() => setOpen(true)}>
        {t('open')}
      </Button>
      <DialogShell open={open} onOpenChange={close} size="sm" height="auto">
        <DialogShell.Header title={t('title')} description={t('confirmDescription')} />
        <DialogShell.Body>
          <FormFieldShell
            id="org-archive-confirm"
            label={t('confirmLabel', { name: view.name })}
            error={error}
          >
            <Input
              id="org-archive-confirm"
              value={confirmName}
              autoComplete="off"
              onChange={(event) => setConfirmName(event.target.value)}
            />
          </FormFieldShell>
        </DialogShell.Body>
        <DialogShell.Footer>
          <Button variant="outline" onClick={() => close(false)}>
            {tCommon('cancel')}
          </Button>
          <Button
            variant="destructive"
            disabled={archive.isPending || confirmName !== view.name}
            onClick={confirm}
          >
            {t('submit')}
          </Button>
        </DialogShell.Footer>
      </DialogShell>
    </div>
  )
}

function RestoreAction({ view }: { view: OrganizationView }) {
  const t = useTranslations('orgAdmin.settings.restore')
  const errorMessage = useOrgErrorMessage()
  const restore = useRestoreTenantOrganization(view.tenant_id)
  const [error, setError] = useState<string | null>(null)
  return (
    <div className="space-y-2">
      <p className="text-sm text-muted-foreground">{t('description')}</p>
      {error ? <p className="text-sm text-status-danger">{error}</p> : null}
      <Button
        disabled={restore.isPending}
        onClick={() =>
          restore.mutate(
            { orgId: view.id, updatedAt: view.updated_at },
            {
              onSuccess: () => toast.success(t('done')),
              onError: (err) => setError(errorMessage(err)),
            },
          )
        }
      >
        {t('submit')}
      </Button>
    </div>
  )
}
