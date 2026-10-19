'use client'

import { useState } from 'react'
import { useTranslations } from 'next-intl'
import { toast } from 'sonner'

import { DialogShell } from '@/components/shared/dialog-shell'
import { DynamicFieldsForm, validateFields } from '@/components/shared/dynamic-fields-form'
import { FormFieldShell } from '@/components/shared/form-field-shell'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Select, SelectContent, SelectItem, SelectTrigger } from '@/components/ui/select'
import { Switch } from '@/components/ui/switch'
import { useOrgErrorMessage } from '@/features/organizations/lib/error-message'
import { useCredentialTypes } from '@/lib/hooks/use-credentials'
import { useTenantCredentialMutations } from '@/lib/hooks/use-tenant-catalog'
import type { TenantCredential, TenantCredentialUpdate } from '@/lib/types/tenant-admin'
import { OrgMultiSelect } from './org-multi-select'

interface CompanyCredentialDialogProps {
  tenantId: string
  /** `null` creates a shared credential. */
  credential: TenantCredential | null
  onClose: () => void
}

/** Create, rotate ("교체") or narrow a company shared credential. Values are write-only. */
export function CompanyCredentialDialog({
  tenantId,
  credential,
  onClose,
}: CompanyCredentialDialogProps) {
  const t = useTranslations('orgAdmin.companySettings.credentials.dialog')
  const tCommon = useTranslations('orgAdmin.common')
  const tValidation = useTranslations('shared.validation')
  const errorMessage = useOrgErrorMessage()
  const { data: definitions } = useCredentialTypes()
  const { create, update } = useTenantCredentialMutations(tenantId)
  // Personal account types (plan 10.4) can never be shared (E-05).
  const shareable = (definitions ?? []).filter((definition) => !definition.personal)
  const [definitionKey, setDefinitionKey] = useState(credential?.definition_key ?? '')
  const [name, setName] = useState(credential?.name ?? '')
  const [active, setActive] = useState(credential ? credential.status === 'active' : true)
  const [orgIds, setOrgIds] = useState<string[]>(credential?.allowed_org_ids ?? [])
  const [replacing, setReplacing] = useState(credential === null)
  const [data, setData] = useState<Record<string, unknown>>({})
  const [errors, setErrors] = useState<Record<string, string>>({})
  const [error, setError] = useState<string | null>(null)
  const definition = shareable.find((item) => item.key === definitionKey)
  const fields = definition?.properties ?? []
  const pending = create.isPending || update.isPending
  const handlers = {
    onSuccess: () => {
      toast.success(t('saved'))
      onClose()
    },
    onError: (err: unknown) => setError(errorMessage(err)),
  }

  function submit() {
    setError(null)
    if (replacing) {
      const found = validateFields(fields, data, tValidation)
      setErrors(found)
      if (Object.keys(found).length > 0) return
    }
    if (credential === null) {
      create.mutate(
        { definition_key: definitionKey, name: name.trim(), data, allowed_org_ids: orgIds },
        handlers,
      )
      return
    }
    const patch: TenantCredentialUpdate = {
      name: name.trim(),
      status: active ? 'active' : 'disabled',
      allowed_org_ids: orgIds,
      ...(replacing ? { data } : {}),
    }
    update.mutate({ id: credential.id, data: patch }, handlers)
  }

  return (
    <DialogShell
      open
      onOpenChange={(open) => (open ? undefined : onClose())}
      size="md"
      height="auto"
    >
      <DialogShell.Header
        title={credential ? t('editTitle', { name: credential.name }) : t('createTitle')}
        description={t('description')}
      />
      <DialogShell.Body>
        <div className="space-y-3">
          {credential === null ? (
            <FormFieldShell id="credential-type" label={t('type')} required>
              <Select
                value={definitionKey}
                onValueChange={(value) => {
                  setDefinitionKey(String(value))
                  setData({})
                  setErrors({})
                }}
              >
                <SelectTrigger id="credential-type" className="w-full">
                  <span>{definition?.display_name ?? t('typePlaceholder')}</span>
                </SelectTrigger>
                <SelectContent>
                  {shareable.map((item) => (
                    <SelectItem key={item.key} value={item.key}>
                      {item.display_name}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </FormFieldShell>
          ) : null}
          <FormFieldShell id="credential-name" label={t('name')} required>
            <Input
              id="credential-name"
              value={name}
              maxLength={200}
              onChange={(event) => setName(event.target.value)}
            />
          </FormFieldShell>
          {credential ? (
            <div className="flex flex-col gap-2">
              <label className="flex items-center gap-2 text-sm">
                <Switch checked={active} onCheckedChange={setActive} />
                {t('active')}
              </label>
              <label className="flex items-center gap-2 text-sm">
                <Switch checked={replacing} onCheckedChange={setReplacing} />
                {t('replace')}
              </label>
            </div>
          ) : null}
          {replacing && definition ? (
            <DynamicFieldsForm fields={fields} value={data} onChange={setData} errors={errors} />
          ) : null}
          <OrgMultiSelect
            tenantId={tenantId}
            legend={t('orgs')}
            value={orgIds}
            onChange={(next) => setOrgIds(next ?? [])}
          />
          {error ? <p className="text-sm text-destructive">{error}</p> : null}
        </div>
      </DialogShell.Body>
      <DialogShell.Footer>
        <Button variant="outline" onClick={onClose}>
          {tCommon('cancel')}
        </Button>
        <Button
          disabled={pending || name.trim() === '' || (credential === null && !definition)}
          onClick={submit}
        >
          {t('save')}
        </Button>
      </DialogShell.Footer>
    </DialogShell>
  )
}
