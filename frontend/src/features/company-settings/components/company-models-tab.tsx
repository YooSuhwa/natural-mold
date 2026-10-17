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
import {
  useTenantCredentials,
  useTenantModelMutations,
  useTenantModels,
} from '@/lib/hooks/use-tenant-catalog'
import type { TenantModel } from '@/lib/types/tenant-admin'
import { CompanyModelDialog } from './company-model-dialog'
import { useOrgNames } from './org-multi-select'

/** S07 "모델": platform models (company only narrows organizations) and company models. */
export function CompanyModelsTab({ tenantId }: { tenantId: string }) {
  const t = useTranslations('orgAdmin.companySettings.models')
  const errorMessage = useOrgErrorMessage()
  const models = useTenantModels(tenantId)
  const credentials = useTenantCredentials(tenantId)
  const { remove } = useTenantModelMutations(tenantId)
  const orgNames = useOrgNames(tenantId)
  const [editing, setEditing] = useState<TenantModel | 'new' | null>(null)
  const [deleting, setDeleting] = useState<TenantModel | null>(null)
  const credentialName = (id: string | null) =>
    credentials.data?.find((credential) => credential.id === id)?.name ?? null

  function keyLabel(model: TenantModel): string {
    if (model.requires_company_key) return t('key.companyRequired')
    const name = credentialName(model.default_credential_id)
    if (name) return name
    return model.source === 'platform' ? t('key.platform') : t('key.none')
  }

  return (
    <SettingsSectionCard
      title={t('title')}
      description={
        models.data && !models.data.allow_system_models ? t('platformDisabled') : t('description')
      }
      actions={
        <Button size="sm" onClick={() => setEditing('new')}>
          {t('add')}
        </Button>
      }
    >
      {models.isLoading ? (
        <Skeleton className="h-32 w-full" />
      ) : !models.data ? (
        <ApiErrorState error={models.error} onRetry={() => void models.refetch()} />
      ) : models.data.items.length === 0 ? (
        <p className="text-sm text-muted-foreground">{t('empty')}</p>
      ) : (
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>{t('name')}</TableHead>
              <TableHead>{t('source')}</TableHead>
              <TableHead>{t('keyColumn')}</TableHead>
              <TableHead>{t('orgs')}</TableHead>
              <TableHead>{t('actions')}</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {models.data.items.map((model) => (
              <TableRow key={model.id}>
                <TableCell>
                  <span className="block text-sm">{model.display_name}</span>
                  <span className="moldy-ui-caption text-muted-foreground">
                    {model.provider} / {model.model_name}
                  </span>
                </TableCell>
                <TableCell>
                  <Badge variant={model.source === 'company' ? 'secondary' : 'outline'}>
                    {t(`sourceLabel.${model.source}`)}
                  </Badge>
                  {model.company_enabled ? null : (
                    <Badge variant="outline">{t('companyDisabled')}</Badge>
                  )}
                </TableCell>
                <TableCell className="text-sm">{keyLabel(model)}</TableCell>
                <TableCell className="text-sm">{orgNames(model.allowed_org_ids)}</TableCell>
                <TableCell>
                  <div className="flex flex-wrap gap-1">
                    <Button
                      size="sm"
                      variant="outline"
                      aria-label={t('editFor', { name: model.display_name })}
                      onClick={() => setEditing(model)}
                    >
                      {t('edit')}
                    </Button>
                    {model.source === 'company' ? (
                      <Button
                        size="sm"
                        variant="outline"
                        aria-label={t('deleteFor', { name: model.display_name })}
                        onClick={() => setDeleting(model)}
                      >
                        {t('delete')}
                      </Button>
                    ) : null}
                  </div>
                </TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      )}
      {editing ? (
        <CompanyModelDialog
          tenantId={tenantId}
          model={editing === 'new' ? null : editing}
          credentials={credentials.data ?? []}
          onClose={() => setEditing(null)}
        />
      ) : null}
      <DeleteConfirmDialog
        open={deleting !== null}
        onOpenChange={(open) => (open ? undefined : setDeleting(null))}
        title={t('deleteTitle')}
        description={t('deleteDescription', { name: deleting?.display_name ?? '' })}
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
