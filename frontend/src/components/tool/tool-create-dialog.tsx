'use client'

import { useMemo, useState } from 'react'
import { useTranslations } from 'next-intl'
import { toast } from 'sonner'

import { DialogShell } from '@/components/shared/dialog-shell'
import { FormFooter } from '@/components/shared/form-footer'
import { Input } from '@/components/ui/input'
import { Textarea } from '@/components/ui/textarea'
import { DynamicFieldsForm, validateFields } from '@/components/shared/dynamic-fields-form'
import { CredentialPicker } from '@/components/credential/credential-picker'
import { DomainIcon } from '@/components/shared/icon'
import { useCreateTool } from '@/lib/hooks/use-tools'
import type { ToolDefinition } from '@/lib/types/tool'
import type { FieldDef } from '@/lib/types/credential'

interface ToolCreateDialogProps {
  definition: ToolDefinition | null
  open: boolean
  onOpenChange: (open: boolean) => void
  onCreated?: (toolId: string) => void
}

export function ToolCreateDialog({
  definition,
  open,
  onOpenChange,
  onCreated,
}: ToolCreateDialogProps) {
  const t = useTranslations('tool.createDialog')
  const tValidation = useTranslations('shared.validation')
  const create = useCreateTool()
  const [name, setName] = useState('')
  const [description, setDescription] = useState('')
  const [parameters, setParameters] = useState<Record<string, unknown>>({})
  const [credentialId, setCredentialId] = useState<string | null>(null)

  function reset() {
    setName('')
    setDescription('')
    setParameters({})
    setCredentialId(null)
  }

  function handleClose(next: boolean) {
    if (!next) reset()
    onOpenChange(next)
  }

  const errors = useMemo(() => {
    if (!definition) return {}
    return validateFields(definition.parameters as FieldDef[], parameters, tValidation, {
      skipRequired: true,
    })
  }, [definition, parameters, tValidation])

  const canSubmit =
    !!definition &&
    name.trim().length > 0 &&
    Object.keys(errors).length === 0 &&
    (!definition.requires_credential || !!credentialId)

  async function handleSubmit() {
    if (!definition || !canSubmit) return
    try {
      const tool = await create.mutateAsync({
        definition_key: definition.key,
        name: name.trim(),
        description: description.trim() || null,
        parameters,
        credential_id: credentialId,
        enabled: true,
      })
      toast.success(t('toast.created'))
      onCreated?.(tool.id)
      handleClose(false)
    } catch (e) {
      toast.error(e instanceof Error ? e.message : t('toast.saveFailed'))
    }
  }

  if (!definition) return null

  return (
    <DialogShell open={open} onOpenChange={handleClose} size="lg" height="fixed">
      <DialogShell.Header
        icon={<DomainIcon iconId={definition.icon_id ?? definition.key} className="size-5" />}
        title={t('title', { toolName: definition.display_name })}
        description={definition.description}
      />
      <DialogShell.Body>
        <div className="space-y-4">
          <div className="space-y-1.5">
            <label htmlFor="tool-name">
              {t('name')} <span className="text-destructive">*</span>
            </label>
            <Input
              id="tool-name"
              value={name}
              placeholder={definition.display_name}
              onChange={(e) => setName(e.target.value)}
            />
          </div>

          <div className="space-y-1.5">
            <label htmlFor="tool-desc">{t('description')}</label>
            <Textarea
              id="tool-desc"
              value={description}
              rows={2}
              placeholder={t('optional')}
              onChange={(e) => setDescription(e.target.value)}
            />
          </div>

          {definition.credential_definition_keys.length > 0 && (
            <div className="space-y-1.5">
              <label htmlFor="tool-credential">
                {t('credential')}{' '}
                {definition.requires_credential && <span className="text-destructive">*</span>}
              </label>
              <CredentialPicker
                value={credentialId}
                onChange={setCredentialId}
                definitionKeys={definition.credential_definition_keys}
              />
            </div>
          )}

          {definition.parameters.length > 0 && (
            <DynamicFieldsForm
              fields={definition.parameters as FieldDef[]}
              value={parameters}
              onChange={setParameters}
              errors={errors}
            />
          )}
        </div>
      </DialogShell.Body>
      <DialogShell.Footer>
        <FormFooter
          onCancel={() => handleClose(false)}
          onSubmit={handleSubmit}
          submitLabel={t('submit')}
          pending={create.isPending}
          disabled={!canSubmit}
        />
      </DialogShell.Footer>
    </DialogShell>
  )
}
