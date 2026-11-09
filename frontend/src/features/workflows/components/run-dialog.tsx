'use client'

import { useState } from 'react'
import { useTranslations } from 'next-intl'
import { toast } from 'sonner'

import { DialogShell } from '@/components/shared/dialog-shell'
import { Button } from '@/components/ui/button'
import { Skeleton } from '@/components/ui/skeleton'
import { ApiError } from '@/lib/api/client'
import { uploadFile } from '@/lib/api/uploads'
import { useStartWorkflowRun, useWorkflowVersion } from '@/lib/hooks/use-workflows'

import { RunInputFields, typedInputs, type InputValues } from './run-input-fields'

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
  const [values, setValues] = useState<InputValues>({})
  const [uploading, setUploading] = useState(0)
  // A file input's value is the upload id (workflow-nodes-plan N8): uploaded on pick.
  const pickFile = async (name: string, file: File | undefined) => {
    if (!file) {
      setValues((current) => ({ ...current, [name]: '' }))
      return
    }
    setUploading((count) => count + 1)
    try {
      const uploaded = await uploadFile(file)
      setValues((current) => ({ ...current, [name]: uploaded.id }))
    } catch (error) {
      toast.error(error instanceof ApiError ? error.message : t('uploadFailed'))
    } finally {
      setUploading((count) => count - 1)
    }
  }
  const inputs = version.data?.definition.inputs ?? []
  const submit = () => {
    start.mutate(typedInputs(inputs, values), {
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
        <RunInputFields
          inputs={inputs}
          values={values}
          onChange={(name, value) => setValues((current) => ({ ...current, [name]: value }))}
          onPickFile={(name, file) => void pickFile(name, file)}
        />
      </DialogShell.Body>
      <DialogShell.Footer>
        <Button variant="outline" onClick={onClose}>
          {t('cancel')}
        </Button>
        <Button disabled={version.isPending || start.isPending || uploading > 0} onClick={submit}>
          {start.isPending ? t('starting') : t('start')}
        </Button>
      </DialogShell.Footer>
    </DialogShell>
  )
}
