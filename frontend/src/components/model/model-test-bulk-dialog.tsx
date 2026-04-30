'use client'

/**
 * Bulk test dialog — runs `<ModelConnectionTest>` per selected model in
 * parallel and shows a running tally. Each child posts its own request, so
 * concurrency comes for free; we just bookkeep results.
 */

import { useEffect, useMemo, useState } from 'react'

import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog'
import { Badge } from '@/components/ui/badge'
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select'
import { ModelConnectionTest } from './model-connection-test'
import { useCredentials, useCredentialTypes } from '@/lib/hooks/use-credentials'
import type { Model, ModelTestResponse } from '@/lib/types/model'

interface ModelTestBulkDialogProps {
  models: Model[]
  open: boolean
  onOpenChange: (open: boolean) => void
}

export function ModelTestBulkDialog({
  models,
  open,
  onOpenChange,
}: ModelTestBulkDialogProps) {
  const { data: credentials } = useCredentials()
  const { data: definitions } = useCredentialTypes()
  const [credentialId, setCredentialId] = useState<string>('')
  const [results, setResults] = useState<Record<string, ModelTestResponse>>({})

  const llmCredentials = useMemo(() => {
    if (!credentials || !definitions) return []
    const llmKeys = new Set(
      definitions.filter((d) => d.category === 'llm').map((d) => d.key),
    )
    return credentials.filter((c) => llmKeys.has(c.definition_key))
  }, [credentials, definitions])

  /* eslint-disable react-hooks/set-state-in-effect */
  useEffect(() => {
    if (!open) return
    if (!credentialId && llmCredentials.length > 0) {
      setCredentialId(llmCredentials[0].id)
    }
  }, [open, credentialId, llmCredentials])
  /* eslint-enable react-hooks/set-state-in-effect */

  /* eslint-disable react-hooks/set-state-in-effect */
  useEffect(() => {
    if (!open) {
      setCredentialId('')
      setResults({})
    }
  }, [open])
  /* eslint-enable react-hooks/set-state-in-effect */

  const completed = Object.keys(results).length
  const total = models.length
  const successCount = Object.values(results).filter((r) => r.success).length
  const failedCount = completed - successCount
  const allDone = total > 0 && completed === total

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-3xl">
        <DialogHeader>
          <DialogTitle>Test {total} model{total === 1 ? '' : 's'}</DialogTitle>
          <DialogDescription>
            Each row runs in parallel against the selected credential.
          </DialogDescription>
        </DialogHeader>

        <div className="space-y-1.5">
          <label htmlFor="bulk-cred" className="text-xs font-medium">
            LLM credential
          </label>
          <Select
            value={credentialId}
            onValueChange={(v) => v && setCredentialId(v)}
            disabled={llmCredentials.length === 0}
          >
            <SelectTrigger id="bulk-cred" className="w-full">
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
          <div className="flex items-center justify-between text-xs text-muted-foreground">
            <span>
              {completed}/{total} 완료
            </span>
            {allDone && (
              <span>
                <Badge variant="secondary" className="mr-1">
                  {successCount} success
                </Badge>
                <Badge variant="destructive">{failedCount} failed</Badge>
              </span>
            )}
          </div>
        )}

        {credentialId && (
          <div className="max-h-[55vh] space-y-3 overflow-auto pr-1">
            {models.map((m) => (
              <div key={m.id} className="space-y-1.5">
                <p className="text-xs font-medium">
                  {m.display_name}{' '}
                  <span className="text-muted-foreground">· {m.model_name}</span>
                </p>
                <ModelConnectionTest
                  key={`${m.id}-${credentialId}`}
                  mode="registered"
                  modelId={m.id}
                  credentialId={credentialId}
                  modelLabel={m.display_name}
                  showCostBanner={false}
                  autoStart
                  onComplete={(r) =>
                    setResults((prev) => ({ ...prev, [m.id]: r }))
                  }
                />
              </div>
            ))}
          </div>
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
