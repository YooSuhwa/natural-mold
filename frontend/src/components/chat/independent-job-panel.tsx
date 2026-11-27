'use client'

import Link from 'next/link'
import { useCancelIndependentJob } from '@/lib/hooks/use-independent-chat-jobs'
import { useTranslations } from 'next-intl'

import { Button } from '@/components/ui/button'
import {
  isOpenIndependentJob,
  type IndependentChatJob,
  type IndependentJobList,
} from '@/lib/types/independent-chat-job'
import { IndependentJobForm } from './independent-job-form'

function chatHref(agentId: string, conversationId: string) {
  return `/agents/${encodeURIComponent(agentId)}/conversations/${encodeURIComponent(conversationId)}`
}

function StopJob({
  job,
  onJob,
}: {
  job: IndependentChatJob
  onJob: (job: IndependentChatJob) => void
}) {
  const t = useTranslations('chat.independentJobs')
  const cancel = useCancelIndependentJob(job, onJob)
  return (
    <>
      {isOpenIndependentJob(job) && job.status !== 'canceling' && (
        <Button
          type="button"
          size="xs"
          variant="ghost"
          disabled={cancel.isPending}
          onClick={() => cancel.mutate()}
        >
          {t('stopJob')}
        </Button>
      )}
      {cancel.isError && (
        <span role="alert" className="text-destructive">
          {t('cancelFailed')}
        </span>
      )}
    </>
  )
}

function JobRow({
  job,
  onJob,
}: {
  job: IndependentChatJob
  onJob: (job: IndependentChatJob) => void
}) {
  const t = useTranslations('chat.independentJobs')
  const action =
    job.status === 'waiting_for_approval'
      ? 'openApproval'
      : job.status === 'completed'
        ? 'openResult'
        : 'openJob'
  return (
    <li className="flex flex-wrap items-center gap-x-3 gap-y-1 py-1 text-sm">
      <span className="font-medium">{job.child_agent_name}</span>
      <span className="text-xs text-muted-foreground">
        {t('reference', { id: job.id.slice(0, 8) })}
      </span>
      <span>{t(`status.${job.status}`)}</span>
      <Link
        className="text-primary-strong-text underline"
        prefetch={false}
        href={chatHref(job.child_agent_id, job.child_conversation_id)}
      >
        {t(action)}
      </Link>
      {job.reason_code === 'independent_runtime_unavailable' && (
        <span className="text-muted-foreground">{t('runtimeUnavailable')}</span>
      )}
      <StopJob job={job} onJob={onJob} />
    </li>
  )
}

export function IndependentJobPanel({
  conversationId,
  runId,
  list,
  onJob,
}: {
  conversationId: string
  runId: string | null
  list: IndependentJobList
  onJob: (job: IndependentChatJob) => void
}) {
  const t = useTranslations('chat.independentJobs')
  if (list.parent_job) {
    const parent = list.parent_job
    if (parent.child_conversation_id !== conversationId) return null
    return (
      <div className="flex items-center gap-3 border-b px-4 py-1.5 text-sm">
        <span>{t(`status.${parent.status}`)}</span>
        <Link
          prefetch={false}
          className="text-primary-strong-text underline"
          href={chatHref(parent.parent_agent_id, parent.parent_conversation_id)}
        >
          {t('openParent')}
        </Link>
        <StopJob job={parent} onJob={onJob} />
      </div>
    )
  }
  const jobs = list.jobs.filter((job) => job.parent_conversation_id === conversationId)
  const canStart = list.enabled && runId !== null && list.targets.length > 0
  if (!canStart && !jobs.length) {
    return list.enabled && runId ? (
      <p className="border-b px-4 py-1 text-xs text-muted-foreground">{t('noTarget')}</p>
    ) : null
  }
  return (
    <section
      aria-label={t('jobs')}
      className="max-h-40 shrink-0 overflow-y-auto border-b px-4 py-2"
    >
      <div className="flex items-center justify-between gap-2">
        {jobs.length > 0 && <h2 className="text-sm font-medium">{t('jobs')}</h2>}
        {canStart && (
          <IndependentJobForm
            key={runId}
            conversationId={conversationId}
            runId={runId}
            targets={list.targets}
            onJob={onJob}
          />
        )}
      </div>
      {jobs.length > 0 && (
        <ul>
          {jobs.map((job) => (
            <JobRow key={job.id} job={job} onJob={onJob} />
          ))}
        </ul>
      )}
    </section>
  )
}
