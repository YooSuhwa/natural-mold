'use client'

import { useMemo, useState } from 'react'
import { useTranslations } from 'next-intl'
import { toast } from 'sonner'
import { ArrowLeft, Search } from 'lucide-react'

import { DialogShell } from '@/components/shared/dialog-shell'
import { FormFooter } from '@/components/shared/form-footer'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { SearchInput } from '@/components/shared/search-input'
import { DomainIcon } from '@/components/shared/icon'
import { Card } from '@/components/ui/card'
import {
  DynamicFieldsForm,
  validateFields,
} from '@/components/shared/dynamic-fields-form'
import { CredentialTestButton } from './credential-test-button'
import { useCredentialTypes } from '@/lib/hooks/use-credentials'
import {
  useCreateCredential,
  useCreateSystemCredential,
} from '@/lib/hooks/use-credentials'
import type { CredentialDefinition, FieldDef } from '@/lib/types/credential'

interface CredentialCreateModalProps {
  open: boolean
  onOpenChange: (open: boolean) => void
  /** Optional starting definition. Skips the catalog step. */
  presetDefinitionKey?: string
  onCreated?: (credentialId: string) => void
  /**
   * ``'system'`` posts to ``/api/system-credentials`` so the resulting row
   * is operator-managed (Fix Agent / builder). Defaults to ``'user'``.
   */
  mode?: 'user' | 'system'
}

export function CredentialCreateModal({
  open,
  onOpenChange,
  presetDefinitionKey,
  onCreated,
  mode = 'user',
}: CredentialCreateModalProps) {
  const t = useTranslations('credentials.create')
  const tValidation = useTranslations('shared.validation')
  const tc = useTranslations('common')
  const { data: definitions } = useCredentialTypes()
  const createUser = useCreateCredential()
  const createSystem = useCreateSystemCredential()
  const create = mode === 'system' ? createSystem : createUser

  const [definitionKey, setDefinitionKey] = useState<string | null>(
    presetDefinitionKey ?? null,
  )
  const [name, setName] = useState('')
  const [data, setData] = useState<Record<string, unknown>>({})
  const [search, setSearch] = useState('')

  function reset() {
    setDefinitionKey(presetDefinitionKey ?? null)
    setName('')
    setData({})
    setSearch('')
  }

  function handleClose(next: boolean) {
    if (!next) reset()
    onOpenChange(next)
  }

  const definition = useMemo<CredentialDefinition | null>(() => {
    if (!definitions || !definitionKey) return null
    return definitions.find((d) => d.key === definitionKey) ?? null
  }, [definitions, definitionKey])

  const filteredDefinitions = useMemo(() => {
    if (!definitions) return []
    const q = search.trim().toLowerCase()
    if (!q) return definitions
    return definitions.filter(
      (d) =>
        d.display_name.toLowerCase().includes(q) ||
        d.key.toLowerCase().includes(q) ||
        d.category.toLowerCase().includes(q),
    )
  }, [definitions, search])

  const errors = useMemo(() => {
    if (!definition) return {}
    return validateFields(definition.properties, data, tValidation)
  }, [definition, data, tValidation])

  const canSubmit =
    definition !== null && name.trim().length > 0 && Object.keys(errors).length === 0

  async function handleSubmit() {
    if (!definition || !canSubmit) return
    try {
      const cred = await create.mutateAsync({
        definition_key: definition.key,
        name: name.trim(),
        data,
        is_shared: false,
      })
      toast.success(t('toast.saved'))
      onCreated?.(cred.id)
      handleClose(false)
    } catch (e) {
      const msg = e instanceof Error ? e.message : t('toast.saveFailed')
      toast.error(msg)
    }
  }

  return (
    <DialogShell open={open} onOpenChange={handleClose} size="lg" height="fixed">
      <DialogShell.Header
        title={definition ? t('titleWithType', { type: definition.display_name }) : t('title')}
        description={
          definition
            ? t('description.form')
            : t('description.catalog')
        }
      />
      <DialogShell.Body>
        {!definition ? (
          <div className="space-y-3">
            <SearchInput
              placeholder={t('searchPlaceholder')}
              value={search}
              onChange={(e) => setSearch(e.target.value)}
            />
            {filteredDefinitions.length === 0 ? (
              <p className="rounded border border-dashed p-6 text-center text-sm text-muted-foreground">
                <Search className="mx-auto mb-2 size-5" /> {t('emptyDefinitions')}
              </p>
            ) : (
              <div
                role="list"
                className="grid max-h-[40vh] gap-2 overflow-auto pr-1 sm:grid-cols-2"
              >
                {filteredDefinitions.map((d) => (
                  <button
                    key={d.key}
                    type="button"
                    role="listitem"
                    onClick={() => {
                      setDefinitionKey(d.key)
                      setName(d.display_name)
                    }}
                    className="flex items-center gap-3 rounded-lg border p-3 text-left transition-colors hover:border-primary/40 hover:bg-muted/40"
                  >
                    <DomainIcon iconId={d.icon_id ?? d.key} className="size-5" />
                    <div className="min-w-0">
                      <p className="truncate text-sm font-medium">{d.display_name}</p>
                      <p className="truncate text-[11px] text-muted-foreground">
                        {d.category}
                      </p>
                    </div>
                  </button>
                ))}
              </div>
            )}
          </div>
        ) : (
          <div className="space-y-4">
            <div className="flex items-center gap-2">
              {!presetDefinitionKey && (
                <Button
                  variant="ghost"
                  size="sm"
                  onClick={() => {
                    setDefinitionKey(null)
                    setName('')
                    setData({})
                  }}
                >
                  <ArrowLeft className="size-4" /> {t('back')}
                </Button>
              )}
              <Card className="flex-1 px-3 py-2 flex items-center gap-2">
                <DomainIcon iconId={definition.icon_id ?? definition.key} />
                <span className="text-sm font-medium">{definition.display_name}</span>
              </Card>
            </div>

            <div className="space-y-1.5">
              <label htmlFor="cred-name" className="text-xs font-medium">
                {t('name')}
              </label>
              <Input
                id="cred-name"
                value={name}
                placeholder={t('namePlaceholder')}
                onChange={(e) => setName(e.target.value)}
              />
            </div>

            <DynamicFieldsForm
              fields={definition.properties as FieldDef[]}
              value={data}
              onChange={setData}
              errors={errors}
            />
          </div>
        )}
      </DialogShell.Body>
      <DialogShell.Footer>
        {definition ? (
          <FormFooter
            onCancel={() => handleClose(false)}
            onSubmit={handleSubmit}
            submitLabel={t('save')}
            pending={create.isPending}
            disabled={!canSubmit}
            extraActions={
              definition.has_test ? (
                <CredentialTestButton
                  preview={{ definition_key: definition.key, data }}
                  variant="outline"
                  size="default"
                />
              ) : undefined
            }
          />
        ) : (
          <Button variant="outline" onClick={() => handleClose(false)}>
            {tc('cancel')}
          </Button>
        )}
      </DialogShell.Footer>
    </DialogShell>
  )
}
