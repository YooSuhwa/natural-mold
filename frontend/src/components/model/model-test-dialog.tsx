'use client'

/**
 * Wrap `<ModelConnectionTest mode="registered">` in a Dialog with an embedded
 * credential picker. Used by the /models row action ("Test"). The bulk
 * "Test Selected" flow uses its own modal — this is only for single-row tests.
 */

import { useEffect, useMemo, useState } from 'react'

import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog'
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

export function ModelTestDialog({
  model,
  open,
  onOpenChange,
}: ModelTestDialogProps) {
  const { data: credentials } = useCredentials()
  const { data: definitions } = useCredentialTypes()
  const [credentialId, setCredentialId] = useState<string>('')

  const llmCredentials = useMemo(() => {
    if (!credentials || !definitions) return []
    const llmKeys = new Set(
      definitions.filter((d) => d.category === 'llm').map((d) => d.key),
    )
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
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-xl">
        <DialogHeader>
          <DialogTitle>Test {model.display_name}</DialogTitle>
          <DialogDescription className="font-mono text-xs">
            {model.provider} · {model.model_name}
          </DialogDescription>
        </DialogHeader>

        <div className="space-y-1.5">
          <label htmlFor="test-cred" className="text-xs font-medium">
            LLM credential
          </label>
          <Select
            value={credentialId}
            onValueChange={(v) => v && setCredentialId(v)}
            disabled={llmCredentials.length === 0}
          >
            <SelectTrigger id="test-cred" className="w-full">
              <SelectValue
                placeholder={
                  llmCredentials.length === 0
                    ? 'No LLM credential available'
                    : 'Select credential'
                }
              />
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
            Add an LLM credential first on the Credentials page.
          </p>
        )}
      </DialogContent>
    </Dialog>
  )
}
