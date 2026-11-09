'use client'

import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import dynamic from 'next/dynamic'
import Link from 'next/link'
import { useRouter } from 'next/navigation'
import {
  ArrowLeftIcon,
  GitCompareIcon,
  HistoryIcon,
  PlayIcon,
  PlusIcon,
  Redo2Icon,
  TableIcon,
  Undo2Icon,
} from 'lucide-react'
import { useTranslations } from 'next-intl'
import { toast } from 'sonner'

import { ApiErrorState } from '@/components/shared/api-error-state'
import { CountedTabs } from '@/components/shared/counted-tabs'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Skeleton } from '@/components/ui/skeleton'
import { ApiError } from '@/lib/api/client'
import { useAgents } from '@/lib/hooks/use-agents'
import {
  usePublishWorkflow,
  useSaveWorkflowDraft,
  useWorkflow,
  useWorkflowIssues,
} from '@/lib/hooks/use-workflows'
import type {
  EdgeWhen,
  WorkflowDefinition,
  WorkflowDetail,
  WorkflowEdge,
} from '@/lib/types/workflow'
import { AddNodeDialog, type AddChoice } from '@/features/workflows/components/add-node-dialog'
import { AiEditPanel, type PendingProposal } from '@/features/workflows/components/ai-edit-panel'
import { IssuesList } from '@/features/workflows/components/issues-list'
import { NodeSettingsPanel } from '@/features/workflows/components/node-settings-panel'
import { RunDialog } from '@/features/workflows/components/run-dialog'
import { StepList } from '@/features/workflows/components/step-list'
import {
  appendAfter,
  apply,
  freeExits,
  insertOnEdge,
  newNode,
  nextId,
  redo,
  removeNode,
  setNext,
  startHistory,
  undo,
  updateNode,
  type History,
} from '@/features/workflows/lib/editor-commands'
import { nodeTitle } from '@/features/workflows/lib/node-title'
import { diffDefinitions } from '@/features/workflows/lib/version-diff'
import type { WorkflowAssistResponse } from '@/lib/types/workflow'
import { cn } from '@/lib/utils'

const AUTOSAVE_MS = 800
// A proposal that adds, removes or changes this many steps shows its plan before the diff.
const LARGE_CHANGE = 3
const DIFF_TONE = {
  added: 'moldy-status-success',
  removed: 'moldy-status-danger',
  changed: 'moldy-status-warn',
} as const

const WorkflowCanvas = dynamic(
  () =>
    import('@/features/workflows/components/workflow-canvas-island').then(
      (mod) => mod.WorkflowCanvasIsland,
    ),
  { ssr: false, loading: () => <Skeleton className="h-full w-full" /> },
)

type SaveStatus = 'saved' | 'pending' | 'saving' | 'conflict' | 'error'
type AddTarget =
  | { kind: 'edge'; edge: WorkflowEdge }
  | { kind: 'after'; nodeId: string; when: EdgeWhen | null }

export function WorkflowEditorClient({ workflowId }: { workflowId: string }) {
  const query = useWorkflow(workflowId)
  const [generation, setGeneration] = useState(0)
  if (query.isPending) {
    return (
      <div className="flex flex-1 flex-col gap-4 p-6">
        <Skeleton className="h-12 w-full" />
        <Skeleton className="h-96 w-full" />
      </div>
    )
  }
  if (query.isError) {
    return (
      <div className="flex flex-1 items-center justify-center p-6">
        <ApiErrorState error={query.error} onRetry={() => void query.refetch()} />
      </div>
    )
  }
  // A reload after a conflict remounts the editor on the server's draft (fresh history).
  return (
    <Editor
      key={`${query.data.id}:${generation}`}
      detail={query.data}
      onReload={async () => {
        await query.refetch()
        setGeneration((value) => value + 1)
      }}
    />
  )
}

function Editor({ detail, onReload }: { detail: WorkflowDetail; onReload: () => Promise<void> }) {
  const t = useTranslations('workflows')
  const [history, setHistory] = useState<History>(() => startHistory(detail.draft_definition))
  const [revision, setRevision] = useState(detail.draft_revision)
  const [status, setStatus] = useState<SaveStatus>('saved')
  const [selectedId, setSelectedId] = useState<string | null>(null)
  const [addTarget, setAddTarget] = useState<AddTarget | null>(null)
  const [showContract, setShowContract] = useState(true)
  const [running, setRunning] = useState(false)
  const [panel, setPanel] = useState<'settings' | 'ai'>('settings')
  const [proposal, setProposal] = useState<PendingProposal | null>(null)
  const router = useRouter()
  const save = useSaveWorkflowDraft(detail.id)
  const publish = usePublishWorkflow(detail.id)
  const issues = useWorkflowIssues(detail.id, revision)
  const agents = useAgents()
  const definition = history.present

  // Autosave: one request at a time, always from the latest saved revision; edits made
  // while a save is in flight are sent right after it. A 409 stops saving until reload.
  const latest = useRef<WorkflowDefinition>(detail.draft_definition)
  const saved = useRef<WorkflowDefinition>(detail.draft_definition)
  const revisionRef = useRef(detail.draft_revision)
  const inFlight = useRef(false)
  const stopped = useRef(false)
  const { mutateAsync } = save
  const flush = useCallback(async () => {
    if (inFlight.current) return
    inFlight.current = true
    try {
      while (!stopped.current && latest.current !== saved.current) {
        const sending = latest.current
        setStatus('saving')
        try {
          const result = await mutateAsync({ revision: revisionRef.current, definition: sending })
          revisionRef.current = result.draft_revision
          saved.current = sending
          setRevision(result.draft_revision)
          setStatus(latest.current === sending ? 'saved' : 'pending')
        } catch (error) {
          stopped.current = error instanceof ApiError && error.status === 409
          setStatus(stopped.current ? 'conflict' : 'error')
          return
        }
      }
    } finally {
      inFlight.current = false
    }
  }, [mutateAsync])
  useEffect(() => {
    latest.current = definition
    const timer = setTimeout(() => void flush(), AUTOSAVE_MS)
    return () => clearTimeout(timer)
  }, [definition, flush])

  const change = useCallback((next: WorkflowDefinition) => {
    setHistory((current) => apply(current, next))
    setStatus('pending')
  }, [])
  const step = useCallback((move: (history: History) => History) => {
    setHistory(move)
    setStatus('pending')
  }, [])

  const agentNames = useMemo(
    () => new Map((agents.data ?? []).map((agent) => [agent.id, agent.name])),
    [agents.data],
  )
  const titleOf = useCallback(
    (nodeId: string) => nodeTitle(definition, nodeId, agentNames, t),
    [definition, agentNames, t],
  )
  const labelOf = useCallback(
    (node: string, path: string) => {
      const field = path.split('/').filter(Boolean).join('.')
      if (node === 'input') {
        const input = definition.inputs.find((item) => `/${item.name}` === path)
        return `${t('kind.start')} · ${input?.label || field}`
      }
      return field ? `${titleOf(node).title} · ${field}` : titleOf(node).title
    },
    [definition, t, titleOf],
  )
  const issueList = useMemo(() => issues.data?.issues ?? [], [issues.data])
  const issueCounts = useMemo(() => {
    const counts = new Map<string, number>()
    for (const issue of issueList) {
      if (issue.node_id) counts.set(issue.node_id, (counts.get(issue.node_id) ?? 0) + 1)
    }
    return counts
  }, [issueList])

  const openAddForSelection = useCallback(() => {
    const nodeId = selectedId ?? 'start'
    const free = freeExits(definition, nodeId)
    if (free.length > 0) {
      setAddTarget({ kind: 'after', nodeId, when: free[0] })
      return
    }
    const outgoing = definition.edges.find((edge) => edge.from === nodeId)
    if (outgoing) setAddTarget({ kind: 'edge', edge: outgoing })
  }, [definition, selectedId])

  const onChoose = useCallback(
    (choice: AddChoice) => {
      if (!addTarget) return
      const id = nextId(definition, choice.type)
      const node = newNode(choice.type, id, { agentId: choice.agentId, label: choice.label })
      change(
        addTarget.kind === 'edge'
          ? insertOnEdge(definition, addTarget.edge, node)
          : appendAfter(definition, addTarget.nodeId, addTarget.when, node),
      )
      setSelectedId(id)
    },
    [addTarget, change, definition],
  )

  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      const target = event.target as HTMLElement | null
      if (target?.closest('input, textarea, [contenteditable="true"]')) return
      const mod = event.metaKey || event.ctrlKey
      if (mod && event.key.toLowerCase() === 'k') {
        event.preventDefault()
        openAddForSelection()
      } else if (mod && event.key.toLowerCase() === 'z') {
        event.preventDefault()
        step(event.shiftKey ? redo : undo)
      }
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [openAddForSelection, step])

  const selected = definition.nodes.find((node) => node.id === selectedId) ?? null
  const agentCount = new Set(
    definition.nodes.flatMap((node) =>
      node.type === 'agent' && node.agent_id ? [node.agent_id] : [],
    ),
  ).size
  const dirty = status !== 'saved'
  const blocked = issueList.length > 0
  const onPublish = () => {
    publish.mutate(revision, {
      onSuccess: (version) => toast.success(t('editor.published', { version: version.version_no })),
      onError: (error) =>
        toast.error(error instanceof ApiError ? error.message : t('editor.saveFailed')),
    })
  }

  const proposed = proposal?.response.definition ?? null
  const review = proposal?.reviewing && proposed ? diffDefinitions(definition, proposed) : null
  const proposalIssueCounts = new Map<string, number>()
  for (const issue of proposal?.response.issues ?? []) {
    if (issue.node_id) {
      proposalIssueCounts.set(issue.node_id, (proposalIssueCounts.get(issue.node_id) ?? 0) + 1)
    }
  }
  const proposalTitleOf = (nodeId: string) =>
    nodeTitle(
      proposed?.nodes.some((node) => node.id === nodeId) ? proposed : definition,
      nodeId,
      agentNames,
      t,
    )
  const onProposal = (response: WorkflowAssistResponse) => {
    if (!response.definition) return
    const { counts } = diffDefinitions(definition, response.definition)
    const size = counts.added + counts.removed + counts.changed
    setProposal({ response, reviewing: size < LARGE_CHANGE })
  }
  const acceptProposal = () => {
    if (!proposed || !proposal) return
    // The proposal changes the draft it was made on; an edit since then would be lost.
    if (revision !== proposal.response.base_revision || status !== 'saved') {
      toast.error(t('ai.stale'))
    } else {
      change(proposed)
      setSelectedId(null)
      toast.success(t('ai.applied'))
    }
    setProposal(null)
  }
  const discardProposal = () => {
    setProposal(null)
    toast(t('ai.discarded'))
  }

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <header className="flex flex-wrap items-center gap-2 border-b px-4 py-2">
        <Button variant="ghost" size="sm" render={<Link href="/workflows" />}>
          <ArrowLeftIcon className="size-4" aria-hidden />
          {t('editor.back')}
        </Button>
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-2">
            <h1 className="truncate text-base font-semibold">{detail.name}</h1>
            <span
              className={
                detail.published_version_no
                  ? 'moldy-status-surface moldy-status-success rounded-full px-2 py-0.5 text-xs'
                  : 'moldy-status-surface moldy-status-warn rounded-full px-2 py-0.5 text-xs'
              }
            >
              {detail.published_version_no
                ? t('published', { version: detail.published_version_no })
                : t('draft')}
            </span>
          </div>
          <p className="truncate text-xs text-muted-foreground">
            {t('editor.reuse', { count: agentCount })}
          </p>
        </div>
        <SaveBadge status={status} onReload={onReload} />
        <Button
          variant="ghost"
          size="icon"
          aria-label={t('editor.undo')}
          disabled={history.past.length === 0}
          onClick={() => step(undo)}
        >
          <Undo2Icon className="size-4" aria-hidden />
        </Button>
        <Button
          variant="ghost"
          size="icon"
          aria-label={t('editor.redo')}
          disabled={history.future.length === 0}
          onClick={() => step(redo)}
        >
          <Redo2Icon className="size-4" aria-hidden />
        </Button>
        <Button variant="outline" size="sm" onClick={openAddForSelection}>
          <PlusIcon className="size-4" aria-hidden />
          <span title={t('editor.addNodeShortcut')}>{t('editor.addNode')}</span>
        </Button>
        <Button
          variant={showContract ? 'secondary' : 'ghost'}
          size="sm"
          aria-pressed={showContract}
          onClick={() => setShowContract((value) => !value)}
        >
          <TableIcon className="size-4" aria-hidden />
          {t('editor.contract')}
        </Button>
        <Badge variant={blocked ? 'destructive' : 'outline'}>
          {blocked ? t('editor.issues', { count: issueList.length }) : t('editor.noIssues')}
        </Badge>
        <Button
          size="sm"
          disabled={dirty || blocked || publish.isPending}
          title={blocked ? t('editor.publishBlocked', { count: issueList.length }) : undefined}
          onClick={onPublish}
        >
          {publish.isPending ? t('editor.publishing') : t('editor.publish')}
        </Button>
        <Button
          size="sm"
          variant="outline"
          disabled={!detail.published_version_no}
          title={detail.published_version_no ? undefined : t('run.needsPublish')}
          onClick={() => setRunning(true)}
        >
          <PlayIcon className="size-4" aria-hidden />
          {t('run.open')}
        </Button>
        <Button variant="ghost" size="sm" render={<Link href={`/workflows/${detail.id}/runs`} />}>
          <HistoryIcon className="size-4" aria-hidden />
          {t('runs.open')}
        </Button>
        <Button
          variant="ghost"
          size="sm"
          render={<Link href={`/workflows/${detail.id}/versions`} />}
        >
          <GitCompareIcon className="size-4" aria-hidden />
          {t('versions.open')}
        </Button>
      </header>
      <div className="flex min-h-0 flex-1">
        <div className="hidden min-h-0 flex-1 flex-col md:flex">
          {review && proposal ? (
            <div
              className="moldy-status-surface moldy-status-info flex flex-wrap items-center gap-2 border-b px-4 py-2"
              role="region"
              aria-label={t('ai.reviewTitle')}
            >
              <span className="text-sm font-medium">{t('ai.reviewTitle')}</span>
              <ul className="flex flex-wrap gap-1" aria-label={t('versions.summary')}>
                {(['added', 'removed', 'changed'] as const).map((kind) => (
                  <li
                    key={kind}
                    className={cn(
                      'moldy-status-surface rounded-full px-2 py-0.5 text-xs',
                      DIFF_TONE[kind],
                    )}
                  >
                    {t(`versions.count.${kind}`, { count: review.counts[kind] })}
                  </li>
                ))}
              </ul>
              {proposal.response.issues.length > 0 ? (
                <span className="text-xs text-status-danger">
                  {t('ai.issues', { count: proposal.response.issues.length })}
                </span>
              ) : null}
              <span className="flex-1" />
              <Button size="sm" variant="outline" onClick={discardProposal}>
                {t('ai.discard')}
              </Button>
              <Button size="sm" onClick={acceptProposal}>
                {t('ai.accept')}
              </Button>
            </div>
          ) : null}
          <div className="min-h-0 flex-1">
            {review ? (
              <WorkflowCanvas
                // A new canvas fits the proposal into view; the draft canvas does the same
                // when the review closes.
                key="review"
                definition={review.merged}
                titleOf={proposalTitleOf}
                showDetail={showContract}
                issueCounts={proposalIssueCounts}
                selectedId={null}
                onSelect={() => undefined}
                diff={review}
              />
            ) : (
              <WorkflowCanvas
                key="draft"
                definition={definition}
                titleOf={titleOf}
                showDetail={showContract}
                issueCounts={issueCounts}
                selectedId={selectedId}
                onSelect={setSelectedId}
                onInsert={(edge) => setAddTarget({ kind: 'edge', edge })}
                onAddAfter={(nodeId, when) => setAddTarget({ kind: 'after', nodeId, when })}
              />
            )}
          </div>
        </div>
        <div className="min-h-0 flex-1 overflow-auto p-4 md:hidden">
          <StepList
            definition={definition}
            titleOf={titleOf}
            issueCounts={issueCounts}
            selectedId={selectedId}
            onSelect={setSelectedId}
          />
        </div>
        <aside className="hidden w-96 shrink-0 flex-col gap-6 overflow-auto border-l p-4 md:flex">
          <CountedTabs
            value={panel}
            onValueChange={(value) => setPanel(value === 'ai' ? 'ai' : 'settings')}
            ariaLabel={t('ai.tabs')}
            tabs={[
              { value: 'settings', label: t('ai.settingsTab') },
              { value: 'ai', label: t('ai.tab') },
            ]}
          />
          {panel === 'ai' ? (
            <AiEditPanel
              workflowId={detail.id}
              revision={revision}
              canSend={status === 'saved'}
              proposal={proposal}
              onProposal={onProposal}
              onReview={() =>
                setProposal((current) => (current ? { ...current, reviewing: true } : current))
              }
            />
          ) : (
            <>
              {selected ? (
                <NodeSettingsPanel
                  key={selected.id}
                  definition={definition}
                  node={selected}
                  labelOf={labelOf}
                  onChange={(node) => change(updateNode(definition, node.id, () => node))}
                  onInputsChange={(inputs) => change({ ...definition, inputs })}
                  onNext={(when, target) => change(setNext(definition, selected.id, when, target))}
                  onDelete={() => {
                    change(removeNode(definition, selected.id))
                    setSelectedId(null)
                  }}
                />
              ) : (
                <p className="text-sm text-muted-foreground">{t('editor.selectHint')}</p>
              )}
              <section className="flex flex-col gap-2">
                <h2 className="text-sm font-semibold">{t('editor.issuesTitle')}</h2>
                <IssuesList issues={issueList} onSelect={setSelectedId} />
              </section>
            </>
          )}
        </aside>
      </div>
      {running && detail.published_version_no ? (
        <RunDialog
          workflowId={detail.id}
          versionNo={detail.published_version_no}
          onClose={() => setRunning(false)}
          onStarted={(runId) => router.push(`/workflows/${detail.id}/runs/${runId}`)}
        />
      ) : null}
      <AddNodeDialog
        open={addTarget !== null}
        onOpenChange={(open) => !open && setAddTarget(null)}
        onChoose={onChoose}
      />
    </div>
  )
}

function SaveBadge({ status, onReload }: { status: SaveStatus; onReload: () => Promise<void> }) {
  const t = useTranslations('workflows.editor')
  if (status === 'conflict') {
    return (
      <span className="flex items-center gap-2 text-sm">
        <span className="text-status-danger">{t('conflict')}</span>
        <Button size="sm" variant="outline" onClick={() => void onReload()}>
          {t('reload')}
        </Button>
      </span>
    )
  }
  const label = {
    saved: t('saved'),
    pending: t('unsaved'),
    saving: t('saving'),
    error: t('saveFailed'),
  }[status]
  return (
    <span
      className={
        status === 'error' ? 'text-status-danger text-sm' : 'text-sm text-muted-foreground'
      }
      aria-live="polite"
    >
      {label}
    </span>
  )
}
