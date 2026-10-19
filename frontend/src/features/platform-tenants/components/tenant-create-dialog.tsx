'use client'

import { useState } from 'react'
import { useTranslations } from 'next-intl'
import { toast } from 'sonner'

import { DialogShell } from '@/components/shared/dialog-shell'
import { FormFieldShell } from '@/components/shared/form-field-shell'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Switch } from '@/components/ui/switch'
import { useCreatePlatformTenant } from '@/lib/hooks/use-platform-tenants'
import type { PlatformTenantCreateResult } from '@/lib/types/platform-tenant'
import { formatDisplayDateTime } from '@/lib/utils/display-format'
import { SLUG_PATTERN, useTenantErrorMessage } from '../lib/messages'
import { LimitField, parseLimit } from './tenant-limit-field'

const EMAIL_PATTERN = /^[^\s@]+@[^\s@]+\.[^\s@]+$/

/** S04 회사 만들기: company, first organization and a one-time admin invitation. */
export function TenantCreateDialog({ onClose }: { onClose: () => void }) {
  const t = useTranslations('appSettings.platformTenants.create')
  const [result, setResult] = useState<PlatformTenantCreateResult | null>(null)
  return (
    <DialogShell
      open
      onOpenChange={(open) => (open ? undefined : onClose())}
      size="sm"
      height="auto"
    >
      <DialogShell.Header title={t('title')} description={t('description')} />
      {result ? (
        <CreatedResult result={result} onClose={onClose} />
      ) : (
        <CreateForm onCreated={setResult} onClose={onClose} />
      )}
    </DialogShell>
  )
}

function CreateForm({
  onCreated,
  onClose,
}: {
  onCreated: (result: PlatformTenantCreateResult) => void
  onClose: () => void
}) {
  const t = useTranslations('appSettings.platformTenants.create')
  const errorMessage = useTenantErrorMessage()
  const create = useCreatePlatformTenant()
  const [form, setForm] = useState({
    name: '',
    slug: '',
    email: '',
    organization: t('defaultOrganization'),
    members: '',
    spend: '',
    runs: '',
    systemModels: false,
  })
  const [error, setError] = useState<string | null>(null)
  const set = (key: keyof typeof form) => (value: string) => setForm({ ...form, [key]: value })
  const slugInvalid = form.slug.length > 0 && !SLUG_PATTERN.test(form.slug)
  const emailInvalid = form.email.length > 0 && !EMAIL_PATTERN.test(form.email.trim())
  const limits = [
    parseLimit(form.members, 'members'),
    parseLimit(form.spend, 'spend'),
    parseLimit(form.runs, 'runs'),
  ]
  const valid =
    form.name.trim() !== '' &&
    SLUG_PATTERN.test(form.slug) &&
    EMAIL_PATTERN.test(form.email.trim()) &&
    form.organization.trim() !== '' &&
    limits.every((limit) => limit.valid)

  function submit() {
    setError(null)
    create.mutate(
      {
        name: form.name.trim(),
        slug: form.slug,
        first_admin_email: form.email.trim(),
        first_organization_name: form.organization.trim(),
        member_limit: limits[0].value,
        monthly_spend_limit: limits[1].value,
        concurrent_run_limit: limits[2].value,
        allow_system_models: form.systemModels,
      },
      {
        onSuccess: (created) => {
          toast.success(t('done', { name: created.tenant.name }))
          onCreated(created)
        },
        onError: (err) => setError(errorMessage(err)),
      },
    )
  }

  return (
    <>
      <DialogShell.Body>
        <div className="space-y-3">
          <FormFieldShell id="tenant-name" label={t('name')} required error={error}>
            <Input
              id="tenant-name"
              value={form.name}
              maxLength={200}
              onChange={(e) => set('name')(e.target.value)}
            />
          </FormFieldShell>
          <FormFieldShell
            id="tenant-slug"
            label={t('slug')}
            required
            description={t('slugHint')}
            error={slugInvalid ? t('slugInvalid') : null}
          >
            <Input
              id="tenant-slug"
              value={form.slug}
              maxLength={40}
              autoComplete="off"
              aria-invalid={slugInvalid || undefined}
              onChange={(e) => set('slug')(e.target.value.toLowerCase())}
            />
          </FormFieldShell>
          <FormFieldShell
            id="tenant-admin-email"
            label={t('adminEmail')}
            required
            error={emailInvalid ? t('emailInvalid') : null}
          >
            <Input
              id="tenant-admin-email"
              type="email"
              value={form.email}
              aria-invalid={emailInvalid || undefined}
              onChange={(e) => set('email')(e.target.value)}
            />
          </FormFieldShell>
          <FormFieldShell id="tenant-first-org" label={t('organization')} required>
            <Input
              id="tenant-first-org"
              value={form.organization}
              maxLength={200}
              onChange={(e) => set('organization')(e.target.value)}
            />
          </FormFieldShell>
          <div className="grid gap-3 sm:grid-cols-3">
            <LimitField
              id="tenant-member-limit"
              kind="members"
              value={form.members}
              onChange={set('members')}
            />
            <LimitField
              id="tenant-spend-limit"
              kind="spend"
              value={form.spend}
              onChange={set('spend')}
            />
            <LimitField
              id="tenant-run-limit"
              kind="runs"
              value={form.runs}
              onChange={set('runs')}
            />
          </div>
          <FormFieldShell
            id="tenant-system-models"
            label={t('systemModels')}
            description={t('systemModelsHint')}
            layout="inline"
          >
            <Switch
              id="tenant-system-models"
              checked={form.systemModels}
              onCheckedChange={(checked) => setForm({ ...form, systemModels: checked })}
            />
          </FormFieldShell>
        </div>
      </DialogShell.Body>
      <DialogShell.Footer>
        <Button variant="outline" onClick={onClose}>
          {t('cancel')}
        </Button>
        <Button disabled={create.isPending || !valid} onClick={submit}>
          {t('submit')}
        </Button>
      </DialogShell.Footer>
    </>
  )
}

async function copyText(text: string): Promise<boolean> {
  try {
    if (!navigator.clipboard?.writeText) return false
    await navigator.clipboard.writeText(text)
    return true
  } catch {
    return false
  }
}

/** The invitation token is a one-time secret: only copied on request, never rendered. */
function CreatedResult({
  result,
  onClose,
}: {
  result: PlatformTenantCreateResult
  onClose: () => void
}) {
  const t = useTranslations('appSettings.platformTenants.create')
  const { invitation } = result
  const token = invitation.token
  return (
    <>
      <DialogShell.Body>
        <div className="space-y-2">
          <p className="text-sm">{t('created', { name: result.tenant.name })}</p>
          <p className="text-sm">{t('invitationFor', { email: invitation.email })}</p>
          <p className="text-xs text-muted-foreground">
            {t('invitationExpires', { date: formatDisplayDateTime(invitation.expires_at) })}
          </p>
          <p className="text-xs text-muted-foreground">{t('noMail')}</p>
          {token ? (
            <Button
              variant="outline"
              size="sm"
              onClick={() => {
                void copyText(token).then((copied) =>
                  copied ? toast.success(t('tokenCopied')) : toast.error(t('tokenCopyFailed')),
                )
              }}
            >
              {t('copyToken')}
            </Button>
          ) : (
            <p className="text-xs text-muted-foreground">{t('tokenUnavailable')}</p>
          )}
        </div>
      </DialogShell.Body>
      <DialogShell.Footer>
        <Button onClick={onClose}>{t('close')}</Button>
      </DialogShell.Footer>
    </>
  )
}
