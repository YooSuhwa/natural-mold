'use client'

import { useState } from 'react'
import { useTranslations } from 'next-intl'
import { toast } from 'sonner'

import { DialogShell } from '@/components/shared/dialog-shell'
import { FormFieldShell } from '@/components/shared/form-field-shell'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Select, SelectContent, SelectItem, SelectTrigger } from '@/components/ui/select'
import { Textarea } from '@/components/ui/textarea'
import { useGuardrailTemplateMutations } from '@/lib/hooks/use-guardrails'
import type { GuardrailTemplateChoice } from '@/lib/types/guardrails'
import { useGuardrailErrorMessage } from '../lib/labels'

interface GuardrailTemplateCreateDialogProps {
  tenantId: string
  choices: GuardrailTemplateChoice[]
  onClose: () => void
}

/** A new company template starts as a copy of a built-in or published template. */
export function GuardrailTemplateCreateDialog({
  tenantId,
  choices,
  onClose,
}: GuardrailTemplateCreateDialogProps) {
  const t = useTranslations('guardrails.templates.createDialog')
  const tKind = useTranslations('guardrails.templateKind')
  const tCommon = useTranslations('orgAdmin.common')
  const errorMessage = useGuardrailErrorMessage()
  const { create } = useGuardrailTemplateMutations(tenantId)
  const [name, setName] = useState('')
  const [description, setDescription] = useState('')
  const [copyFrom, setCopyFrom] = useState(choices[0]?.ref ?? '')
  const [error, setError] = useState<string | null>(null)
  const source = choices.find((item) => item.ref === copyFrom)
  const valid = name.trim() !== '' && source !== undefined

  function submit() {
    setError(null)
    create.mutate(
      { name: name.trim(), description: description.trim(), copy_from: copyFrom },
      {
        onSuccess: () => {
          toast.success(t('created'))
          onClose()
        },
        onError: (err) => setError(errorMessage(err)),
      },
    )
  }

  return (
    <DialogShell
      open
      onOpenChange={(open) => (open ? undefined : onClose())}
      size="md"
      height="auto"
    >
      <DialogShell.Header title={t('title')} description={t('description')} />
      <DialogShell.Body>
        <div className="space-y-3">
          <FormFieldShell id="guardrail-template-name" label={t('name')} required>
            <Input
              id="guardrail-template-name"
              value={name}
              maxLength={80}
              onChange={(event) => setName(event.target.value)}
            />
          </FormFieldShell>
          <FormFieldShell id="guardrail-template-description" label={t('templateDescription')}>
            <Textarea
              id="guardrail-template-description"
              value={description}
              maxLength={500}
              onChange={(event) => setDescription(event.target.value)}
            />
          </FormFieldShell>
          <FormFieldShell id="guardrail-template-copy" label={t('copyFrom')} required>
            <Select value={copyFrom} onValueChange={(value) => setCopyFrom(String(value))}>
              <SelectTrigger id="guardrail-template-copy" className="w-full">
                <span>{source ? source.name : ''}</span>
              </SelectTrigger>
              <SelectContent>
                {choices.map((item) => (
                  <SelectItem key={item.ref} value={item.ref}>
                    {`${item.name} · ${tKind(item.kind)} v${item.version}`}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </FormFieldShell>
          {error ? <p className="text-sm text-destructive">{error}</p> : null}
        </div>
      </DialogShell.Body>
      <DialogShell.Footer>
        <Button variant="outline" onClick={onClose}>
          {tCommon('cancel')}
        </Button>
        <Button disabled={!valid || create.isPending} onClick={submit}>
          {t('submit')}
        </Button>
      </DialogShell.Footer>
    </DialogShell>
  )
}
