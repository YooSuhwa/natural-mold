'use client'

import { useState } from 'react'
import { useTranslations } from 'next-intl'
import { toast } from 'sonner'

import { ApiErrorState } from '@/components/shared/api-error-state'
import { DeleteConfirmDialog } from '@/components/shared/delete-confirm-dialog'
import { SettingsSectionCard } from '@/components/shared/settings-section-card'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Skeleton } from '@/components/ui/skeleton'
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table'
import { useOrgErrorMessage } from '@/features/organizations/lib/error-message'
import { useTenantCredentialMutations, useTenantCredentials } from '@/lib/hooks/use-tenant-catalog'
import type { TenantCredential } from '@/lib/types/tenant-admin'
import { CompanyCredentialDialog } from './company-credential-dialog'
import { useOrgNames } from './org-multi-select'

/** S07 "공용 자격증명" (`scope=tenant`): values are never shown, only field names. */
export function CompanyCredentialsTab({ tenantId }: { tenantId: string }) {
  const t = useTranslations('orgAdmin.companySettings.credentials')
  const errorMessage = useOrgErrorMessage()
  const credentials = useTenantCredentials(tenantId)
  const { remove } = useTenantCredentialMutations(tenantId)
  const orgNames = useOrgNames(tenantId)
  const [editing, setEditing] = useState<TenantCredential | 'new' | null>(null)
  const [deleting, setDeleting] = useState<TenantCredential | null>(null)

  return (
    <SettingsSectionCard
      title={t('title')}
      description={t('description')}
      actions={
        <Button size="sm" onClick={() => setEditing('new')}>
          {t('add')}
        </Button>
      }
    >
      {credentials.isLoading ? (
        <Skeleton className="h-32 w-full" />
      ) : !credentials.data ? (
        <ApiErrorState error={credentials.error} onRetry={() => void credentials.refetch()} />
      ) : credentials.data.length === 0 ? (
        <p className="text-sm text-muted-foreground">{t('empty')}</p>
      ) : (
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>{t('name')}</TableHead>
              <TableHead>{t('fields')}</TableHead>
              <TableHead>{t('orgs')}</TableHead>
              <TableHead>{t('actions')}</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {credentials.data.map((credential) => (
              <TableRow key={credential.id}>
                <TableCell>
                  <span className="block text-sm">{credential.name}</span>
                  <span className="moldy-ui-caption text-muted-foreground">
                    {credential.definition_key}
                  </span>
                  {credential.status === 'active' ? null : (
                    <Badge variant="outline">{t('disabled')}</Badge>
                  )}
                </TableCell>
                <TableCell className="text-sm">{credential.field_keys.join(', ')}</TableCell>
                <TableCell className="text-sm">{orgNames(credential.allowed_org_ids)}</TableCell>
                <TableCell>
                  <div className="flex flex-wrap gap-1">
                    <Button
                      size="sm"
                      variant="outline"
                      aria-label={t('editFor', { name: credential.name })}
                      onClick={() => setEditing(credential)}
                    >
                      {t('edit')}
                    </Button>
                    <Button
                      size="sm"
                      variant="outline"
                      aria-label={t('deleteFor', { name: credential.name })}
                      onClick={() => setDeleting(credential)}
                    >
                      {t('delete')}
                    </Button>
                  </div>
                </TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      )}
      {editing ? (
        <CompanyCredentialDialog
          tenantId={tenantId}
          credential={editing === 'new' ? null : editing}
          onClose={() => setEditing(null)}
        />
      ) : null}
      <DeleteConfirmDialog
        open={deleting !== null}
        onOpenChange={(open) => (open ? undefined : setDeleting(null))}
        title={t('deleteTitle')}
        description={t('deleteDescription', { name: deleting?.name ?? '' })}
        isPending={remove.isPending}
        onConfirm={() => {
          if (!deleting) return
          remove.mutate(deleting.id, {
            onSuccess: () => toast.success(t('deleted')),
            onError: (err) => toast.error(errorMessage(err)),
            onSettled: () => setDeleting(null),
          })
        }}
      />
    </SettingsSectionCard>
  )
}
