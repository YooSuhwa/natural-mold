'use client'

import { useId, useState } from 'react'
import { useStartIndependentJob } from '@/lib/hooks/use-independent-chat-jobs'
import { useTranslations } from 'next-intl'
import { Button } from '@/components/ui/button'
import { DialogShell } from '@/components/shared/dialog-shell'
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select'
import { Textarea } from '@/components/ui/textarea'
import type { IndependentChatJob, IndependentJobList } from '@/lib/types/independent-chat-job'

export function IndependentJobForm({
  conversationId,
  runId,
  targets,
  childTaskId,
  onJob,
}: {
  conversationId: string
  runId: string
  targets: IndependentJobList['targets']
  childTaskId?: string
  onJob: (job: IndependentChatJob) => void
}) {
  const t = useTranslations('chat.independentJobs')
  const selectId = useId()
  const [open, setOpen] = useState(false)
  const [instruction, setInstruction] = useState('')
  const [chosen, setChosen] = useState<string | null>(null)
  const targetId = chosen ?? (targets.length === 1 ? targets[0].id : '')
  const validTarget = targets.some((target) => target.id === targetId)
  const { start, ineligible } = useStartIndependentJob(conversationId, runId, (job) => {
    onJob(job)
    setOpen(false)
    setInstruction('')
    setChosen(null)
  })

  return (
    <>
      <Button
        variant="outline"
        size="sm"
        type="button"
        onClick={() => {
          start.reset()
          setOpen(true)
        }}
      >
        {t('start')}
      </Button>
      <DialogShell
        size="md"
        height="auto"
        open={open}
        onOpenChange={(value) => {
          if (!start.isPending) {
            setOpen(value)
            start.reset()
          }
        }}
      >
        <DialogShell.Header title={t('start')} description={t('howItRuns')} />
        <DialogShell.Body>
          <form
            className="grid gap-3"
            onSubmit={(event) => {
              event.preventDefault()
              if (validTarget && instruction.trim() && !start.isPending)
                start.mutate({ targetId, instruction, childTaskId })
            }}
          >
            <div className="grid gap-1 text-sm">
              <label htmlFor={selectId}>{t('target')}</label>
              <Select
                value={validTarget ? targetId : null}
                disabled={start.isPending}
                items={targets.map((target) => ({ value: target.id, label: target.name }))}
                onValueChange={setChosen}
              >
                <SelectTrigger id={selectId}>
                  <SelectValue placeholder={t('selectTarget')} />
                </SelectTrigger>
                <SelectContent>
                  {targets.map((target) => (
                    <SelectItem key={target.id} value={target.id}>
                      {target.name}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
            <label className="grid gap-1 text-sm">
              {t('instruction')}
              <Textarea
                required
                maxLength={16000}
                value={instruction}
                disabled={start.isPending}
                onChange={(event) => setInstruction(event.target.value)}
              />
            </label>
            {start.isError && (
              <p role="alert" className="text-sm text-destructive">
                {t(ineligible ? 'targetUnavailable' : 'startFailed')}
              </p>
            )}
            <div className="flex justify-end gap-2">
              <Button
                type="button"
                variant="ghost"
                onClick={() => setOpen(false)}
                disabled={start.isPending}
              >
                {t('back')}
              </Button>
              <Button
                type="submit"
                disabled={!validTarget || !instruction.trim() || start.isPending}
              >
                {t('submit')}
              </Button>
            </div>
          </form>
        </DialogShell.Body>
      </DialogShell>
    </>
  )
}
