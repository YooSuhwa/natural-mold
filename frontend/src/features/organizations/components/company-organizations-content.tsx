'use client'

import { useState } from 'react'
import { useTranslations } from 'next-intl'
import { toast } from 'sonner'

import { DialogShell } from '@/components/shared/dialog-shell'
import { FormFieldShell } from '@/components/shared/form-field-shell'
import { SettingsSectionCard } from '@/components/shared/settings-section-card'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Select, SelectContent, SelectItem, SelectTrigger } from '@/components/ui/select'
import { Skeleton } from '@/components/ui/skeleton'
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table'
import {
  useCreateTenantOrganization,
  useRestoreTenantOrganization,
  useTenantOrganizations,
} from '@/lib/hooks/use-organizations'
import {
  isTenantOrganizationAdminPage,
  type DefaultMemberRole,
  type OrganizationSummary,
  type TenantOrganizationAdministratorResult,
  type TenantOrganizationCreateResult,
} from '@/lib/types/organization'
import { formatDisplayDateTime } from '@/lib/utils/display-format'
import { useCursorPager } from '../lib/cursor-pager'
import { useOrgErrorMessage } from '../lib/error-message'
import { DirectoryPager } from './directory-pager'
import { OrgPageFrame } from './org-page-frame'
import { OrganizationArchiveAction } from './organization-archive-dialog'

const MEMBER_ROLES: readonly DefaultMemberRole[] = ['member', 'builder']

export function CompanyOrganizationsContent() {
  const t = useTranslations('orgAdmin.company')
  return (
    <OrgPageFrame title={t('title')} description={t('description')}>
      {(organization) => <CompanyOrganizationsBody organization={organization} />}
    </OrgPageFrame>
  )
}

function CompanyOrganizationsBody({ organization }: { organization: OrganizationSummary }) {
  const t = useTranslations('orgAdmin.company')
  const errorMessage = useOrgErrorMessage()
  const tenantId = organization.tenant_id
  const pager = useCursorPager()
  const page = useTenantOrganizations(tenantId, { cursor: pager.cursor })
  const restore = useRestoreTenantOrganization(tenantId)
  const [creating, setCreating] = useState(false)
  const [created, setCreated] = useState<TenantOrganizationCreateResult | null>(null)

  const data = page.data
  const isAdmin = data ? isTenantOrganizationAdminPage(data) : false

  return (
    <div className="space-y-4">
      <SettingsSectionCard
        title={t('list.title', { company: organization.tenant_name })}
        description={data ? (isAdmin ? t('list.adminView') : t('list.auditorView')) : undefined}
        actions={
          isAdmin ? (
            <Button size="sm" onClick={() => setCreating(true)}>
              {t('create.open')}
            </Button>
          ) : null
        }
      >
        {page.isLoading ? (
          <Skeleton className="h-32 w-full" />
        ) : !data ? (
          <p className="text-sm text-muted-foreground">{errorMessage(page.error)}</p>
        ) : data.items.length === 0 ? (
          <p className="text-sm text-muted-foreground">{t('list.empty')}</p>
        ) : isTenantOrganizationAdminPage(data) ? (
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>{t('list.name')}</TableHead>
                <TableHead>{t('list.status')}</TableHead>
                <TableHead>{t('list.members')}</TableHead>
                <TableHead>{t('list.actions')}</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {data.items.map((org) => (
                <TableRow key={org.id}>
                  <TableCell>
                    <span className="block text-sm">{org.name}</span>
                    {org.is_default ? <Badge variant="secondary">{t('list.default')}</Badge> : null}
                  </TableCell>
                  <TableCell>
                    <Badge variant={org.status === 'active' ? 'secondary' : 'outline'}>
                      {t(`status.${org.status}`)}
                    </Badge>
                  </TableCell>
                  <TableCell>{org.member_count}</TableCell>
                  <TableCell>
                    {org.status === 'archived' ? (
                      <Button
                        size="sm"
                        variant="outline"
                        disabled={restore.isPending}
                        onClick={() =>
                          restore.mutate(
                            { orgId: org.id, updatedAt: org.updated_at },
                            {
                              onSuccess: () => toast.success(t('restoreDone')),
                              onError: (err) => toast.error(errorMessage(err)),
                            },
                          )
                        }
                      >
                        {t('restore')}
                      </Button>
                    ) : (
                      <OrganizationArchiveAction tenantId={tenantId} organization={org} />
                    )}
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        ) : (
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>{t('list.name')}</TableHead>
                <TableHead>{t('list.members')}</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {data.items.map((org) => (
                <TableRow key={org.id}>
                  <TableCell>{org.name}</TableCell>
                  <TableCell>{org.member_count}</TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        )}
        <DirectoryPager
          canGoBack={pager.canGoBack}
          hasMore={data?.has_more ?? false}
          onBack={pager.back}
          onNext={() => pager.next(data?.next_cursor ?? null)}
        />
      </SettingsSectionCard>

      {creating ? (
        <CreateOrganizationDialog
          tenantId={tenantId}
          onClose={() => setCreating(false)}
          onCreated={(result) => {
            setCreating(false)
            setCreated(result)
          }}
        />
      ) : null}
      {created ? (
        <CreatedOrganizationDialog result={created} onClose={() => setCreated(null)} />
      ) : null}
    </div>
  )
}

function CreateOrganizationDialog({
  tenantId,
  onClose,
  onCreated,
}: {
  tenantId: string
  onClose: () => void
  onCreated: (result: TenantOrganizationCreateResult) => void
}) {
  const t = useTranslations('orgAdmin.company.create')
  const tCommon = useTranslations('orgAdmin.common')
  const errorMessage = useOrgErrorMessage()
  const create = useCreateTenantOrganization(tenantId)
  const [name, setName] = useState('')
  const [role, setRole] = useState<DefaultMemberRole>('member')
  const [adminEmail, setAdminEmail] = useState('')
  const [error, setError] = useState<string | null>(null)
  const trimmedName = name.trim()
  const trimmedEmail = adminEmail.trim()

  function submit() {
    setError(null)
    create.mutate(
      {
        name: trimmedName,
        default_member_role: role,
        administrator: trimmedEmail ? { email: trimmedEmail } : null,
      },
      { onSuccess: onCreated, onError: (err) => setError(errorMessage(err)) },
    )
  }

  return (
    <DialogShell
      open
      onOpenChange={(open) => (open ? undefined : onClose())}
      size="sm"
      height="auto"
    >
      <DialogShell.Header title={t('title')} description={t('description')} />
      <DialogShell.Body>
        <div className="space-y-3">
          <FormFieldShell id="new-org-name" label={t('name')} required error={error}>
            <Input
              id="new-org-name"
              value={name}
              maxLength={200}
              onChange={(event) => setName(event.target.value)}
            />
          </FormFieldShell>
          <FormFieldShell id="new-org-role" label={t('defaultRole')}>
            <Select
              value={role}
              onValueChange={(value) => {
                if (value === 'member' || value === 'builder') setRole(value)
              }}
            >
              <SelectTrigger id="new-org-role" className="w-full">
                <span>{t(`role.${role}`)}</span>
              </SelectTrigger>
              <SelectContent>
                {MEMBER_ROLES.map((option) => (
                  <SelectItem key={option} value={option}>
                    {t(`role.${option}`)}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </FormFieldShell>
          <FormFieldShell
            id="new-org-admin"
            label={t('adminEmail')}
            description={t('adminEmailHint')}
          >
            <Input
              id="new-org-admin"
              type="email"
              value={adminEmail}
              onChange={(event) => setAdminEmail(event.target.value)}
            />
          </FormFieldShell>
        </div>
      </DialogShell.Body>
      <DialogShell.Footer>
        <Button variant="outline" onClick={onClose}>
          {tCommon('cancel')}
        </Button>
        <Button disabled={create.isPending || trimmedName.length === 0} onClick={submit}>
          {t('submit')}
        </Button>
      </DialogShell.Footer>
    </DialogShell>
  )
}

function CreatedOrganizationDialog({
  result,
  onClose,
}: {
  result: TenantOrganizationCreateResult
  onClose: () => void
}) {
  const t = useTranslations('orgAdmin.company.created')
  const tCommon = useTranslations('orgAdmin.common')
  return (
    <DialogShell
      open
      onOpenChange={(open) => (open ? undefined : onClose())}
      size="sm"
      height="auto"
    >
      <DialogShell.Header
        title={t('title')}
        description={t('description', { name: result.organization.name })}
      />
      <DialogShell.Body>
        <AdministratorResult administrator={result.administrator} />
      </DialogShell.Body>
      <DialogShell.Footer>
        <Button onClick={onClose}>{tCommon('close')}</Button>
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

/**
 * The invitation token is a one-time secret: it is only ever handed to the
 * clipboard on explicit user action — never rendered, logged or persisted.
 */
export function AdministratorResult({
  administrator,
}: {
  administrator: TenantOrganizationAdministratorResult
}) {
  const t = useTranslations('orgAdmin.company.created')
  if (administrator.kind === 'none') {
    return <p className="text-sm text-muted-foreground">{t('noAdministrator')}</p>
  }
  if (administrator.kind === 'member') {
    return <p className="text-sm text-muted-foreground">{t('memberAdministrator')}</p>
  }
  const { invitation } = administrator
  const token = invitation.token
  return (
    <div className="space-y-2">
      <p className="text-sm">{t('invitationSent', { email: invitation.email })}</p>
      <p className="text-xs text-muted-foreground">
        {t('invitationExpires', { date: formatDisplayDateTime(invitation.expires_at) })}
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
  )
}
