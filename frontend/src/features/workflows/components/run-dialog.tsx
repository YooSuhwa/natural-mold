'use client'

import { useState } from 'react'
import { useTranslations } from 'next-intl'
import { toast } from 'sonner'

import { DialogShell } from '@/components/shared/dialog-shell'
import { Button } from '@/components/ui/button'
import { Checkbox } from '@/components/ui/checkbox'
import { Input } from '@/components/ui/input'
import { Skeleton } from '@/components/ui/skeleton'
import { Textarea } from '@/components/ui/textarea'
import { ApiError } from '@/lib/api/client'
import { useStartWorkflowRun, useWorkflowVersion } from '@/lib/hooks/use-workflows'
import type { WorkflowInput } from '@/lib/types/workflow'

type Values = Record<string, string | boolean>

function typed(input: WorkflowInput, raw: string | boolean | undefined): unknown {
  if (input.type === 'boolean') return raw === true
  if (raw === undefined || raw === '') return undefined
  if (input.type === 'number' || input.type === 'integer') return Number(raw)
  return raw
}

/** 시험 실행: the published version's inputs as a form (the run uses that version). */
export function RunDialog({
  workflowId,
  versionNo,
  onClose,
  onStarted,
}: {
  workflowId: string
  versionNo: number
  onClose: () => void
  onStarted: (runId: string) => void
}) {
  const t = useTranslations('workflows.run')
  const version = useWorkflowVersion(workflowId, versionNo)
  const start = useStartWorkflowRun(workflowId)
  const [values, setValues] = useState<Values>({})
  const inputs = version.data?.definition.inputs ?? []
  const submit = () => {
    const payload: Record<string, unknown> = {}
    for (const input of inputs) {
      const value = typed(input, values[input.name])
      if (value !== undefined) payload[input.name] = value
    }
    start.mutate(payload, {
      onSuccess: (run) => {
        toast.success(t('started', { id: run.id.slice(0, 8) }))
        onStarted(run.id)
      },
      onError: (error) => {
        const problems = (error instanceof ApiError ? error.details?.problems : undefined) as
          | { name: string; message: string }[]
          | undefined
        toast.error(
          problems?.length
            ? problems.map((p) => `${p.name}: ${p.message}`).join(' / ')
            : error instanceof ApiError
              ? error.message
              : t('failed'),
        )
      },
    })
  }
  return (
    <DialogShell open onOpenChange={(open) => !open && onClose()} size="md">
      <DialogShell.Header title={t('title', { version: versionNo })} description={t('hint')} />
      <DialogShell.Body className="flex flex-col gap-4">
        {version.isPending ? <Skeleton className="h-24 w-full" /> : null}
        {inputs.length === 0 && !version.isPending ? (
          <p className="text-sm text-muted-foreground">{t('noInputs')}</p>
        ) : null}
        {inputs.map((input) => (
          <label key={input.name} className="flex flex-col gap-1.5 text-sm">
            <span>
              {input.label || input.name}
              {input.required ? <span className="text-status-danger"> *</span> : null}
            </span>
            {input.type === 'boolean' ? (
              <Checkbox
                checked={values[input.name] === true}
                onCheckedChange={(checked) =>
                  setValues((current) => ({ ...current, [input.name]: checked === true }))
                }
              />
            ) : input.type === 'string' ? (
              <Textarea
                rows={3}
                maxLength={input.max_length ?? undefined}
                value={String(values[input.name] ?? '')}
                onChange={(event) =>
                  setValues((current) => ({ ...current, [input.name]: event.target.value }))
                }
              />
            ) : (
              <Input
                type="number"
                step={input.type === 'integer' ? 1 : 'any'}
                value={String(values[input.name] ?? '')}
                onChange={(event) =>
                  setValues((current) => ({ ...current, [input.name]: event.target.value }))
                }
              />
            )}
          </label>
        ))}
      </DialogShell.Body>
      <DialogShell.Footer>
        <Button variant="outline" onClick={onClose}>
          {t('cancel')}
        </Button>
        <Button disabled={version.isPending || start.isPending} onClick={submit}>
          {start.isPending ? t('starting') : t('start')}
        </Button>
      </DialogShell.Footer>
    </DialogShell>
  )
}
