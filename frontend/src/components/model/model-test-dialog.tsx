'use client'

/**
 * Wrap `<ModelConnectionTest mode="registered">` in a Dialog with an embedded
 * credential picker. Used by the /models row action ("Test"). The bulk
 * "Test Selected" flow uses its own modal — this is only for single-row tests.
 */

import { useEffect, useMemo, useState } from 'react'
import { useTranslations } from 'next-intl'

import { DialogShell } from '@/components/shared/dialog-shell'
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select'
import { ModelConnectionTest } from './model-connection-test'
import { useCredentials, useCredentialTypes } from '@/lib/hooks/use-credentials'
import { resolveCredentialForModel } from '@/lib/utils/credential-resolution'
import type { Model } from '@/lib/types/model'

interface ModelTestDialogProps {
  model: Model | null
  open: boolean
  onOpenChange: (open: boolean) => void
}

export function ModelTestDialog({ model, open, onOpenChange }: ModelTestDialogProps) {
  const t = useTranslations('model.testDialog')
  const { data: credentials } = useCredentials()
  const { data: definitions } = useCredentialTypes()
  const [credentialId, setCredentialId] = useState<string>('')

  const llmCredentials = useMemo(() => {
    if (!credentials || !definitions) return []
    const llmKeys = new Set(definitions.filter((d) => d.category === 'llm').map((d) => d.key))
    return credentials.filter((c) => llmKeys.has(c.definition_key))
  }, [credentials, definitions])

  // Pre-select the credential the user picked at Add-model time
  // (default_credential_id), with provider-match / first-LLM as fallbacks.
  // Mirrors the picks made by /models row [Check] and the Health panel.
  /* eslint-disable react-hooks/set-state-in-effect */
  useEffect(() => {
    if (!open || !model) return
    if (credentialId) return
    const picked = resolveCredentialForModel(model, llmCredentials)
    if (picked) setCredentialId(picked)
  }, [open, model, credentialId, llmCredentials])
  /* eslint-enable react-hooks/set-state-in-effect */

  // Reset when the dialog closes so the next open doesn't re-show a stale test.
  /* eslint-disable react-hooks/set-state-in-effect */
  useEffect(() => {
    if (!open) setCredentialId('')
  }, [open])
  /* eslint-enable react-hooks/set-state-in-effect */

  if (!model) return null

  return (
    <DialogShell open={open} onOpenChange={onOpenChange} size="md" height="auto">
      <DialogShell.Header
        title={t('title', { name: model.display_name })}
        description={
          <span className="font-mono text-xs">
            {model.provider} · {model.model_name}
          </span>
        }
      />
      <DialogShell.Body>
        <div className="space-y-1.5">
          <label htmlFor="test-cred">{t('llmCredential')}</label>
          <Select
            value={credentialId}
            onValueChange={(v) => v && setCredentialId(v)}
            disabled={llmCredentials.length === 0}
          >
            <SelectTrigger id="test-cred" className="w-full">
              {/* base-ui Select can't auto-extract a label from JSX-wrapped
                  SelectItem children, so it falls back to the raw value
                  (UUID). Function children let us render the credential's
                  name directly. */}
              <SelectValue
                placeholder={
                  llmCredentials.length === 0 ? t('noCredential') : t('selectCredential')
                }
              >
                {(selected) =>
                  llmCredentials.find((c) => c.id === selected)?.name ??
                  (llmCredentials.length === 0 ? t('noCredential') : t('selectCredential'))
                }
              </SelectValue>
            </SelectTrigger>
            <SelectContent>
              {llmCredentials.map((c) => (
                <SelectItem key={c.id} value={c.id}>
                  {c.name}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>

        {credentialId && (
          <ModelConnectionTest
            key={`${model.id}-${credentialId}`}
            mode="registered"
            modelId={model.id}
            credentialId={credentialId}
            modelLabel={model.display_name}
            autoStart
          />
        )}

        {llmCredentials.length === 0 && (
          <p className="rounded border border-dashed p-3 text-xs text-muted-foreground">
            {t('emptyCredential')}
          </p>
        )}
      </DialogShell.Body>
    </DialogShell>
  )
}
