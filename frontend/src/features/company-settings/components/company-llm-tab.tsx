'use client'

import { useState } from 'react'
import { useTranslations } from 'next-intl'
import { toast } from 'sonner'

import { ApiErrorState } from '@/components/shared/api-error-state'
import { DialogShell } from '@/components/shared/dialog-shell'
import { FormFieldShell } from '@/components/shared/form-field-shell'
import { SettingsSectionCard } from '@/components/shared/settings-section-card'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Select, SelectContent, SelectItem, SelectTrigger } from '@/components/ui/select'
import { Skeleton } from '@/components/ui/skeleton'
import { useOrgErrorMessage } from '@/features/organizations/lib/error-message'
import {
  useTenantCredentials,
  useTenantLlmSettings,
  useUpdateTenantLlmSetting,
} from '@/lib/hooks/use-tenant-catalog'
import type { TenantCredential, TenantLlmSetting } from '@/lib/types/tenant-admin'

/** S07 "서비스 LLM": per role, follow the platform default or override with a company key. */
export function CompanyLlmTab({ tenantId }: { tenantId: string }) {
  const t = useTranslations('orgAdmin.companySettings.llm')
  const errorMessage = useOrgErrorMessage()
  const settings = useTenantLlmSettings(tenantId)
  const credentials = useTenantCredentials(tenantId)
  const update = useUpdateTenantLlmSetting(tenantId)
  const [editing, setEditing] = useState<TenantLlmSetting | null>(null)

  return (
    <SettingsSectionCard
      title={t('title')}
      description={
        settings.data && !settings.data.allow_system_models
          ? t('platformDisabled')
          : t('description')
      }
    >
      {settings.isLoading ? (
        <Skeleton className="h-32 w-full" />
      ) : !settings.data ? (
        <ApiErrorState error={settings.error} onRetry={() => void settings.refetch()} />
      ) : (
        <ul className="divide-y divide-border">
          {settings.data.items.map((setting) => (
            <li key={setting.role} className="flex flex-col gap-2 py-3 sm:flex-row sm:items-center">
              <div className="min-w-0 flex-1 space-y-1">
                <span className="block text-sm font-medium">{t(`role.${setting.role}`)}</span>
                <span className="moldy-ui-caption text-muted-foreground">
                  {setting.inherits
                    ? t('inherits')
                    : t('override', {
                        credential: setting.credential_name ?? '-',
                        model: setting.model_name ?? '-',
                      })}
                </span>
              </div>
              <Badge variant={setting.configured ? 'secondary' : 'outline'}>
                {t(setting.configured ? 'configured' : 'notConfigured')}
              </Badge>
              <div className="flex gap-1">
                <Button
                  size="sm"
                  variant="outline"
                  aria-label={t('editFor', { role: t(`role.${setting.role}`) })}
                  onClick={() => setEditing(setting)}
                >
                  {t('edit')}
                </Button>
                {setting.inherits ? null : (
                  <Button
                    size="sm"
                    variant="outline"
                    disabled={update.isPending}
                    aria-label={t('inheritFor', { role: t(`role.${setting.role}`) })}
                    onClick={() =>
                      update.mutate(
                        { role: setting.role, credential_id: null, model_name: null },
                        {
                          onSuccess: () => toast.success(t('saved')),
                          onError: (err) => toast.error(errorMessage(err)),
                        },
                      )
                    }
                  >
                    {t('inherit')}
                  </Button>
                )}
              </div>
            </li>
          ))}
        </ul>
      )}
      {editing ? (
        <LlmOverrideDialog
          tenantId={tenantId}
          setting={editing}
          credentials={credentials.data ?? []}
          onClose={() => setEditing(null)}
        />
      ) : null}
    </SettingsSectionCard>
  )
}

function LlmOverrideDialog({
  tenantId,
  setting,
  credentials,
  onClose,
}: {
  tenantId: string
  setting: TenantLlmSetting
  credentials: TenantCredential[]
  onClose: () => void
}) {
  const t = useTranslations('orgAdmin.companySettings.llm')
  const tCommon = useTranslations('orgAdmin.common')
  const errorMessage = useOrgErrorMessage()
  const update = useUpdateTenantLlmSetting(tenantId)
  const [credentialId, setCredentialId] = useState(setting.credential_id ?? '')
  const [modelName, setModelName] = useState(setting.model_name ?? '')
  const [error, setError] = useState<string | null>(null)
  const label = credentials.find((item) => item.id === credentialId)?.name ?? t('pickCredential')

  return (
    <DialogShell
      open
      onOpenChange={(open) => (open ? undefined : onClose())}
      size="sm"
      height="auto"
    >
      <DialogShell.Header title={t(`role.${setting.role}`)} description={t('dialogDescription')} />
      <DialogShell.Body>
        <div className="space-y-3">
          <FormFieldShell id="llm-credential" label={t('credential')} required>
            <Select value={credentialId} onValueChange={(value) => setCredentialId(String(value))}>
              <SelectTrigger id="llm-credential" className="w-full">
                <span>{label}</span>
              </SelectTrigger>
              <SelectContent>
                {credentials.map((item) => (
                  <SelectItem key={item.id} value={item.id}>
                    {item.name}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </FormFieldShell>
          <FormFieldShell id="llm-model" label={t('model')} required error={error}>
            <Input
              id="llm-model"
              value={modelName}
              onChange={(e) => setModelName(e.target.value)}
            />
          </FormFieldShell>
        </div>
      </DialogShell.Body>
      <DialogShell.Footer>
        <Button variant="outline" onClick={onClose}>
          {tCommon('cancel')}
        </Button>
        <Button
          disabled={!credentialId || modelName.trim() === '' || update.isPending}
          onClick={() =>
            update.mutate(
              { role: setting.role, credential_id: credentialId, model_name: modelName.trim() },
              {
                onSuccess: () => {
                  toast.success(t('saved'))
                  onClose()
                },
                onError: (err) => setError(errorMessage(err)),
              },
            )
          }
        >
          {t('save')}
        </Button>
      </DialogShell.Footer>
    </DialogShell>
  )
}
