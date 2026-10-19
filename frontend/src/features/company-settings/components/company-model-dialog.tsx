'use client'

import { useState } from 'react'
import { useTranslations } from 'next-intl'
import { toast } from 'sonner'

import { DialogShell } from '@/components/shared/dialog-shell'
import { FormFieldShell } from '@/components/shared/form-field-shell'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Select, SelectContent, SelectItem, SelectTrigger } from '@/components/ui/select'
import { useOrgErrorMessage } from '@/features/organizations/lib/error-message'
import { useTenantModelMutations } from '@/lib/hooks/use-tenant-catalog'
import type { TenantCredential, TenantModel } from '@/lib/types/tenant-admin'
import { OrgMultiSelect } from './org-multi-select'

const NO_CREDENTIAL = 'none'

interface CompanyModelDialogProps {
  tenantId: string
  /** `null` creates a company model. */
  model: TenantModel | null
  credentials: TenantCredential[]
  onClose: () => void
}

/** Platform models only narrow organizations; company models are fully editable (E-07). */
export function CompanyModelDialog({
  tenantId,
  model,
  credentials,
  onClose,
}: CompanyModelDialogProps) {
  const t = useTranslations('orgAdmin.companySettings.models.dialog')
  const tCommon = useTranslations('orgAdmin.common')
  const errorMessage = useOrgErrorMessage()
  const { create, update } = useTenantModelMutations(tenantId)
  const platform = model?.source === 'platform'
  const [provider, setProvider] = useState(model?.provider ?? '')
  const [modelName, setModelName] = useState(model?.model_name ?? '')
  const [displayName, setDisplayName] = useState(model?.display_name ?? '')
  const [baseUrl, setBaseUrl] = useState(model?.base_url ?? '')
  const [credentialId, setCredentialId] = useState(model?.default_credential_id ?? NO_CREDENTIAL)
  const [orgIds, setOrgIds] = useState<string[] | null>(model ? model.allowed_org_ids : [])
  const [error, setError] = useState<string | null>(null)
  const pending = create.isPending || update.isPending
  const credential = credentialId === NO_CREDENTIAL ? null : credentialId
  const valid =
    platform ||
    (displayName.trim() !== '' &&
      (model !== null || (provider.trim() !== '' && modelName.trim() !== '')))
  const handlers = {
    onSuccess: () => {
      toast.success(t('saved'))
      onClose()
    },
    onError: (err: unknown) => setError(errorMessage(err)),
  }

  function submit() {
    setError(null)
    if (model === null) {
      create.mutate(
        {
          provider: provider.trim(),
          model_name: modelName.trim(),
          display_name: displayName.trim(),
          base_url: baseUrl.trim() || null,
          default_credential_id: credential,
          allowed_org_ids: orgIds ?? [],
        },
        handlers,
      )
      return
    }
    const data = platform
      ? { allowed_org_ids: orgIds }
      : {
          display_name: displayName.trim(),
          base_url: baseUrl.trim() || null,
          default_credential_id: credential,
          allowed_org_ids: orgIds ?? [],
        }
    update.mutate({ id: model.id, data }, handlers)
  }

  const credentialLabel =
    credentials.find((item) => item.id === credentialId)?.name ?? t('noCredential')

  return (
    <DialogShell
      open
      onOpenChange={(open) => (open ? undefined : onClose())}
      size="md"
      height="auto"
    >
      <DialogShell.Header
        title={model ? t('editTitle', { name: model.display_name }) : t('createTitle')}
        description={platform ? t('platformDescription') : t('companyDescription')}
      />
      <DialogShell.Body>
        <div className="space-y-3">
          {model === null ? (
            <>
              <FormFieldShell id="model-provider" label={t('provider')} required>
                <Input
                  id="model-provider"
                  value={provider}
                  onChange={(e) => setProvider(e.target.value)}
                />
              </FormFieldShell>
              <FormFieldShell id="model-name" label={t('modelName')} required>
                <Input
                  id="model-name"
                  value={modelName}
                  onChange={(e) => setModelName(e.target.value)}
                />
              </FormFieldShell>
            </>
          ) : null}
          {platform ? null : (
            <>
              <FormFieldShell id="model-display-name" label={t('displayName')} required>
                <Input
                  id="model-display-name"
                  value={displayName}
                  maxLength={200}
                  onChange={(e) => setDisplayName(e.target.value)}
                />
              </FormFieldShell>
              <FormFieldShell
                id="model-base-url"
                label={t('baseUrl')}
                description={t('baseUrlHint')}
              >
                <Input
                  id="model-base-url"
                  value={baseUrl}
                  onChange={(e) => setBaseUrl(e.target.value)}
                />
              </FormFieldShell>
              <FormFieldShell id="model-credential" label={t('credential')}>
                <Select
                  value={credentialId}
                  onValueChange={(value) => setCredentialId(String(value))}
                >
                  <SelectTrigger id="model-credential" className="w-full">
                    <span>{credentialLabel}</span>
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value={NO_CREDENTIAL}>{t('noCredential')}</SelectItem>
                    {credentials.map((item) => (
                      <SelectItem key={item.id} value={item.id}>
                        {item.name}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </FormFieldShell>
            </>
          )}
          <OrgMultiSelect
            tenantId={tenantId}
            legend={t('orgs')}
            value={orgIds}
            onChange={setOrgIds}
            allowAll={platform}
          />
          {error ? <p className="text-sm text-destructive">{error}</p> : null}
        </div>
      </DialogShell.Body>
      <DialogShell.Footer>
        <Button variant="outline" onClick={onClose}>
          {tCommon('cancel')}
        </Button>
        <Button disabled={!valid || pending} onClick={submit}>
          {t('save')}
        </Button>
      </DialogShell.Footer>
    </DialogShell>
  )
}
