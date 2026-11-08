'use client'

import Link from 'next/link'
import { useTranslations } from 'next-intl'

import { EmptyState } from '@/components/shared/empty-state'
import { PageShell } from '@/components/shared/page-shell'
import { Button } from '@/components/ui/button'
import { Skeleton } from '@/components/ui/skeleton'
import { RunStatusChip } from '@/features/workflows/components/run-status'
import { useWorkflow, useWorkflowRuns } from '@/lib/hooks/use-workflows'
import { formatDisplayDateTime } from '@/lib/utils/display-format'

/** 실행 기록: the workflow's recent runs, newest first. */
export function RunsPageClient({ workflowId }: { workflowId: string }) {
  const t = useTranslations('workflows.runs')
  const workflow = useWorkflow(workflowId)
  const runs = useWorkflowRuns(workflowId)
  return (
    <PageShell
      title={t('listTitle', { name: workflow.data?.name ?? '' })}
      description={t('listDescription')}
      isError={runs.isError}
      onRetry={() => void runs.refetch()}
      action={
        <Button variant="outline" render={<Link href={`/workflows/${workflowId}`} />}>
          {t('backToEditor')}
        </Button>
      }
    >
      {runs.isPending ? (
        <Skeleton className="h-40 w-full" />
      ) : runs.data && runs.data.length > 0 ? (
        <ul className="moldy-panel flex flex-col divide-y">
          {runs.data.map((run) => (
            <li key={run.id}>
              <Link
                href={`/workflows/${workflowId}/runs/${run.id}`}
                className="flex flex-wrap items-center gap-3 px-4 py-3 hover:bg-accent"
              >
                <RunStatusChip status={run.status} />
                <span className="font-mono text-xs text-muted-foreground">
                  {run.id.slice(0, 8)}
                </span>
                <span className="text-sm">
                  {run.version_no ? t('version', { version: run.version_no }) : null}
                </span>
                <span className="text-sm text-muted-foreground">
                  {formatDisplayDateTime(run.created_at)}
                </span>
                {run.error_message ? (
                  <span className="min-w-0 flex-1 truncate text-sm text-status-danger">
                    {run.error_message}
                  </span>
                ) : null}
              </Link>
            </li>
          ))}
        </ul>
      ) : (
        <EmptyState title={t('empty')} description={t('emptyHint')} />
      )}
    </PageShell>
  )
}
