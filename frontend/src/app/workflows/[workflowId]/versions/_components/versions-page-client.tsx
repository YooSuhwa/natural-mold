'use client'

import { useState } from 'react'
import dynamic from 'next/dynamic'
import Link from 'next/link'
import { useRouter } from 'next/navigation'
import { useTranslations } from 'next-intl'
import { toast } from 'sonner'

import { DeleteConfirmDialog } from '@/components/shared/delete-confirm-dialog'
import { EmptyState } from '@/components/shared/empty-state'
import { PageShell } from '@/components/shared/page-shell'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Skeleton } from '@/components/ui/skeleton'
import { nodeTitle } from '@/features/workflows/lib/node-title'
import { diffDefinitions, type DiffStatus } from '@/features/workflows/lib/version-diff'
import { ApiError } from '@/lib/api/client'
import { useAgents } from '@/lib/hooks/use-agents'
import {
  useSaveWorkflowDraft,
  useWorkflow,
  useWorkflowVersion,
  useWorkflowVersions,
} from '@/lib/hooks/use-workflows'
import { cn } from '@/lib/utils'
import { formatDisplayDateTime } from '@/lib/utils/display-format'
import type { WorkflowDefinition, WorkflowDetail } from '@/lib/types/workflow'

const WorkflowCanvas = dynamic(
  () =>
    import('@/features/workflows/components/workflow-canvas-island').then(
      (mod) => mod.WorkflowCanvasIsland,
    ),
  { ssr: false, loading: () => <Skeleton className="h-full w-full" /> },
)

const NO_ISSUES = new Map<string, number>()
const EMPTY: WorkflowDefinition = {
  schema_version: 'moldy.workflow.v1',
  inputs: [],
  nodes: [],
  edges: [],
}
const TONE: Record<Exclude<DiffStatus, 'same'>, string> = {
  added: 'moldy-status-success',
  removed: 'moldy-status-danger',
  changed: 'moldy-status-warn',
}

type Mode = 'previous' | 'draft'

function isStart(definition: WorkflowDefinition, nodeId: string): boolean {
  return definition.nodes.some((node) => node.id === nodeId && node.type === 'start')
}

/** 버전 기록 (plan 3.7): published versions, a colour comparison on the canvas, restore. */
export function VersionsPageClient({ workflowId }: { workflowId: string }) {
  const t = useTranslations('workflows.versions')
  const workflow = useWorkflow(workflowId)
  const versions = useWorkflowVersions(workflowId)
  const [picked, setPicked] = useState<number | null>(null)
  const selectedNo = picked ?? versions.data?.[0]?.version_no ?? null
  return (
    <PageShell
      title={t('title', { name: workflow.data?.name ?? '' })}
      description={t('description')}
      isError={versions.isError || workflow.isError}
      onRetry={() => void versions.refetch()}
      action={
        <Button variant="outline" render={<Link href={`/workflows/${workflowId}`} />}>
          {t('backToEditor')}
        </Button>
      }
    >
      {versions.isPending || workflow.isPending ? (
        <Skeleton className="h-96 w-full" />
      ) : versions.data && versions.data.length > 0 && workflow.data && selectedNo ? (
        <div className="grid gap-4 lg:grid-cols-4">
          <ol className="moldy-panel flex flex-col gap-1 p-3" aria-label={t('list')}>
            {versions.data.map((version) => (
              <li key={version.version_no}>
                <button
                  type="button"
                  aria-current={version.version_no === selectedNo ? 'true' : undefined}
                  onClick={() => setPicked(version.version_no)}
                  className={cn(
                    'flex w-full flex-col items-start gap-0.5 rounded-md px-3 py-2 text-left hover:bg-accent',
                    version.version_no === selectedNo && 'bg-accent',
                  )}
                >
                  <span className="flex items-center gap-2 text-sm font-medium">
                    {t('versionNo', { version: version.version_no })}
                    {version.version_no === workflow.data.published_version_no ? (
                      <Badge variant="outline">{t('live')}</Badge>
                    ) : null}
                  </span>
                  <span className="text-xs text-muted-foreground">
                    {formatDisplayDateTime(version.published_at)}
                  </span>
                </button>
              </li>
            ))}
          </ol>
          <div className="lg:col-span-3">
            <Comparison
              key={selectedNo}
              workflow={workflow.data}
              versionNo={selectedNo}
              hasPrevious={versions.data.some((item) => item.version_no < selectedNo)}
            />
          </div>
        </div>
      ) : (
        <EmptyState title={t('empty')} description={t('emptyHint')} />
      )}
    </PageShell>
  )
}

function Comparison({
  workflow,
  versionNo,
  hasPrevious,
}: {
  workflow: WorkflowDetail
  versionNo: number
  hasPrevious: boolean
}) {
  const t = useTranslations('workflows.versions')
  const tw = useTranslations('workflows')
  const router = useRouter()
  const [mode, setMode] = useState<Mode>('previous')
  const [restoring, setRestoring] = useState(false)
  const target = useWorkflowVersion(workflow.id, versionNo)
  const previous = useWorkflowVersion(workflow.id, hasPrevious ? versionNo - 1 : null)
  const agents = useAgents()
  const save = useSaveWorkflowDraft(workflow.id)
  if (target.isPending || (mode === 'previous' && hasPrevious && previous.isPending)) {
    return <Skeleton className="h-96 w-full" />
  }
  if (!target.data) return null
  const version = target.data
  // "Previous": what this version changed. "Draft": what restoring it would change.
  const base = mode === 'draft' ? workflow.draft_definition : (previous.data?.definition ?? EMPTY)
  const diff = diffDefinitions(base, version.definition)
  const agentNames = new Map((agents.data ?? []).map((agent) => [agent.id, agent.name]))
  const titleOf = (nodeId: string) => {
    const owner = version.definition.nodes.some((node) => node.id === nodeId)
      ? version.definition
      : base
    return nodeTitle(owner, nodeId, agentNames, tw)
  }
  const changed = [...diff.nodes].filter(
    (entry): entry is [string, Exclude<DiffStatus, 'same'>] => entry[1] !== 'same',
  )
  const restore = () =>
    save.mutate(
      { revision: workflow.draft_revision, definition: version.definition },
      {
        onSuccess: () => {
          toast.success(t('restored', { version: versionNo }))
          router.push(`/workflows/${workflow.id}`)
        },
        onError: (error) =>
          toast.error(
            error instanceof ApiError && error.status === 409
              ? t('restoreConflict')
              : t('restoreFailed'),
          ),
      },
    )
  return (
    <div className="moldy-panel flex flex-col gap-3 p-4" role="region" aria-label={t('compare')}>
      <div className="flex flex-wrap items-center gap-2">
        <div className="flex gap-1" role="group" aria-label={t('compareWith')}>
          {(['previous', 'draft'] as const).map((item) => (
            <Button
              key={item}
              size="sm"
              variant={mode === item ? 'secondary' : 'ghost'}
              aria-pressed={mode === item}
              onClick={() => setMode(item)}
            >
              {t(`mode.${item}`)}
            </Button>
          ))}
        </div>
        <p className="min-w-0 flex-1 text-sm text-muted-foreground">
          {mode === 'draft'
            ? t('draftHint', { version: versionNo })
            : hasPrevious
              ? t('previousHint', { version: versionNo, previous: versionNo - 1 })
              : t('firstHint')}
        </p>
        <Button size="sm" variant="outline" onClick={() => setRestoring(true)}>
          {t('restore')}
        </Button>
      </div>
      <ul className="flex flex-wrap gap-2" aria-label={t('summary')}>
        {(['added', 'removed', 'changed'] as const).map((status) => (
          <li
            key={status}
            className={cn('moldy-status-surface rounded-full px-2 py-0.5 text-xs', TONE[status])}
          >
            {t(`count.${status}`, { count: diff.counts[status] })}
          </li>
        ))}
      </ul>
      <div className="hidden h-160 md:block">
        <WorkflowCanvas
          definition={diff.merged}
          titleOf={titleOf}
          showDetail
          issueCounts={NO_ISSUES}
          selectedId={null}
          onSelect={() => undefined}
          diff={diff}
        />
      </div>
      {changed.length > 0 ? (
        <ul className="flex flex-col divide-y" aria-label={t('changes')}>
          {changed.map(([nodeId, status]) => (
            <li key={nodeId} className="flex items-center gap-3 py-2 text-sm">
              <span
                className={cn(
                  'moldy-status-surface rounded-full px-2 py-0.5 text-xs',
                  TONE[status],
                )}
              >
                {t(`status.${status}`)}
              </span>
              <span className="min-w-0 truncate">{titleOf(nodeId).title}</span>
              {status === 'changed' && diff.inputsChanged && isStart(diff.merged, nodeId) ? (
                <span className="text-xs text-muted-foreground">{t('inputsChanged')}</span>
              ) : null}
            </li>
          ))}
        </ul>
      ) : (
        <p className="text-sm text-muted-foreground">{t('noChanges')}</p>
      )}
      <DeleteConfirmDialog
        open={restoring}
        onOpenChange={setRestoring}
        title={t('restoreTitle', { version: versionNo })}
        description={t('restoreDescription')}
        confirmLabel={t('restore')}
        isPending={save.isPending}
        onConfirm={restore}
      />
    </div>
  )
}
