'use client'

import { useMemo, useState } from 'react'
import { Loader2Icon } from 'lucide-react'
import { useTranslations } from 'next-intl'
import { toast } from 'sonner'

import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogDescription,
  DialogFooter,
} from '@/components/ui/dialog'
import {
  Select,
  SelectTrigger,
  SelectValue,
  SelectContent,
  SelectItem,
} from '@/components/ui/select'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import {
  useCredentialProviders,
  useCreateCredential,
  useUpdateCredential,
} from '@/lib/hooks/use-credentials'
import type { Credential } from '@/lib/types'

interface CredentialFormDialogProps {
  open: boolean
  onOpenChange: (open: boolean) => void
  editingCredential?: Credential | null
  defaultProvider?: string
  onCreated?: (credential: Credential) => void
  onUpdated?: (credential: Credential) => void
}

export function CredentialFormDialog(props: CredentialFormDialogProps) {
  const { open, onOpenChange } = props
  return (
    <Dialog
      open={open}
      onOpenChange={(v) => {
        if (!v) onOpenChange(false)
      }}
    >
      <DialogContent className="sm:max-w-md">
        {open && <FormBody {...props} />}
      </DialogContent>
    </Dialog>
  )
}

function FormBody({
  onOpenChange,
  editingCredential = null,
  defaultProvider,
  onCreated,
  onUpdated,
}: CredentialFormDialogProps) {
  const t = useTranslations('connections')
  const tc = useTranslations('common')
  const { data: providers } = useCredentialProviders()
  const createCredential = useCreateCredential()
  const updateCredential = useUpdateCredential()

  const initialProviderKey = editingCredential?.provider_name ?? defaultProvider ?? ''
  const initialProvider = providers?.find((p) => p.key === initialProviderKey)

  const [formProvider, setFormProvider] = useState<string>(initialProviderKey)
  const [formName, setFormName] = useState<string>(
    () => editingCredential?.name ?? initialProvider?.name ?? '',
  )
  const [formValues, setFormValues] = useState<Record<string, string>>(() => {
    if (editingCredential || !initialProvider) return {}
    const init: Record<string, string> = {}
    for (const f of initialProvider.fields) init[f.key] = f.default ?? ''
    return init
  })

  const selectedProvider = useMemo(
    () => providers?.find((p) => p.key === formProvider),
    [providers, formProvider],
  )

  function handleProviderChange(key: string | null) {
    if (!key) return
    setFormProvider(key)
    const provider = providers?.find((p) => p.key === key)
    if (!provider) return
    const init: Record<string, string> = {}
    for (const f of provider.fields) init[f.key] = f.default ?? ''
    setFormValues(init)
    if (!formName) setFormName(provider.name)
  }

  async function handleSubmit() {
    if (!selectedProvider) return
    const data: Record<string, string> = {}
    for (const f of selectedProvider.fields) {
      if (formValues[f.key]) data[f.key] = formValues[f.key]
    }

    try {
      if (editingCredential) {
        const updated = await updateCredential.mutateAsync({
          id: editingCredential.id,
          data: { name: formName, data },
        })
        toast.success(t('toast.updated'))
        onUpdated?.(updated)
      } else {
        const created = await createCredential.mutateAsync({
          name: formName,
          credential_type: selectedProvider.credential_type,
          provider_name: selectedProvider.key,
          data,
        })
        toast.success(t('toast.created'))
        onCreated?.(created)
      }
      onOpenChange(false)
    } catch {
      toast.error(editingCredential ? t('toast.updateFailed') : t('toast.createFailed'))
    }
  }

  const isPending = createCredential.isPending || updateCredential.isPending
  const submitDisabled = !formProvider || !formName.trim() || isPending
  const providerLocked = !!editingCredential || !!defaultProvider

  return (
    <>
      <DialogHeader>
        <DialogTitle>{editingCredential ? t('editTitle') : t('addNew')}</DialogTitle>
        <DialogDescription>{t('pageDescription')}</DialogDescription>
      </DialogHeader>

      <div className="space-y-4 py-2">
        <div className="space-y-2">
          <label className="text-sm font-medium">{t('provider')}</label>
          <Select
            value={formProvider}
            onValueChange={handleProviderChange}
            disabled={providerLocked}
          >
            <SelectTrigger className="w-full">
              <SelectValue placeholder={t('providerPlaceholder')}>
                {(v: string) => providers?.find((p) => p.key === v)?.name ?? ''}
              </SelectValue>
            </SelectTrigger>
            <SelectContent>
              {providers?.map((p) => (
                <SelectItem key={p.key} value={p.key}>
                  {p.name}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>

        <div className="space-y-2">
          <label className="text-sm font-medium">{t('name')}</label>
          <Input
            value={formName}
            onChange={(e) => setFormName(e.target.value)}
            placeholder={t('namePlaceholder')}
          />
        </div>

        {selectedProvider?.fields.map((field) => (
          <div key={field.key} className="space-y-2">
            <label htmlFor={`cred-${field.key}`} className="text-sm font-medium">
              {field.label}
            </label>
            <Input
              id={`cred-${field.key}`}
              type={field.secret ? 'password' : 'text'}
              placeholder={editingCredential ? '********' : field.default ?? ''}
              value={formValues[field.key] ?? ''}
              onChange={(e) =>
                setFormValues((prev) => ({ ...prev, [field.key]: e.target.value }))
              }
            />
          </div>
        ))}

        {selectedProvider && (
          <p className="text-xs text-muted-foreground">{t('form.secretHint')}</p>
        )}
      </div>

      <DialogFooter>
        <Button variant="outline" onClick={() => onOpenChange(false)}>
          {tc('cancel')}
        </Button>
        <Button onClick={handleSubmit} disabled={submitDisabled}>
          {isPending && <Loader2Icon className="size-4 animate-spin" data-icon="inline-start" />}
          {editingCredential ? tc('save') : tc('create')}
        </Button>
      </DialogFooter>
    </>
  )
}
