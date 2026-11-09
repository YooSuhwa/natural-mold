'use client'

import { useState } from 'react'
import { useTranslations } from 'next-intl'
import { toast } from 'sonner'

import { Button } from '@/components/ui/button'
import { Textarea } from '@/components/ui/textarea'
import { ApiError } from '@/lib/api/client'
import { useDecideApproval } from '@/lib/hooks/use-approvals'

/** The written answer to a workflow's "ask" step (workflow-nodes-plan N5), in the run's
 * card and in the inbox: the only decision is an answer; the time limit takes "timeout". */
export function AskAnswerForm({ requestId }: { requestId: string }) {
  const t = useTranslations('workflows.ask')
  const decide = useDecideApproval()
  const [answer, setAnswer] = useState('')
  const text = answer.trim()
  return (
    <form
      className="flex flex-col gap-2"
      onSubmit={(event) => {
        event.preventDefault()
        if (!text) return
        decide.mutate(
          { id: requestId, decisions: [{ type: 'approve', message: text }] },
          {
            onError: (error) =>
              toast.error(error instanceof ApiError ? error.message : t('failed')),
          },
        )
      }}
    >
      <Textarea
        rows={3}
        maxLength={2000}
        value={answer}
        aria-label={t('answer')}
        placeholder={t('placeholder')}
        onChange={(event) => setAnswer(event.target.value)}
      />
      <Button type="submit" size="sm" className="self-start" disabled={!text || decide.isPending}>
        {t('send')}
      </Button>
    </form>
  )
}
