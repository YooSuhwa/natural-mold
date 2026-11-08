'use client'

import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import dynamic from 'next/dynamic'
import Link from 'next/link'
import { ArrowLeftIcon, PlusIcon, Redo2Icon, Trash2Icon, Undo2Icon } from 'lucide-react'
import { useTranslations } from 'next-intl'
import { toast } from 'sonner'

import { ApiErrorState } from '@/components/shared/api-error-state'
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
import { IssuesList } from '@/features/workflows/components/issues-list'
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
  startHistory,
  undo,
  type History,
} from '@/features/workflows/lib/editor-commands'

const AUTOSAVE_MS = 800

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
    (nodeId: string) => {
      const node = definition.nodes.find((item) => item.id === nodeId)
      if (!node) return { title: nodeId, subtitle: '' }
      const kind = t(`kind.${node.type}`)
      const agentName = node.type === 'agent' ? agentNames.get(node.agent_id) : undefined
      return { title: node.label || agentName || kind, subtitle: kind }
    },
    [definition, agentNames, t],
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
  const dirty = status !== 'saved'
  const blocked = issueList.length > 0
  const onPublish = () => {
    publish.mutate(revision, {
      onSuccess: (version) => toast.success(t('editor.published', { version: version.version_no })),
      onError: (error) =>
        toast.error(error instanceof ApiError ? error.message : t('editor.saveFailed')),
    })
  }

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <header className="flex flex-wrap items-center gap-2 border-b px-4 py-2">
        <Button variant="ghost" size="sm" render={<Link href="/workflows" />}>
          <ArrowLeftIcon className="size-4" aria-hidden />
          {t('editor.back')}
        </Button>
        <h1 className="min-w-0 flex-1 truncate text-base font-semibold">{detail.name}</h1>
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
      </header>
      <div className="flex min-h-0 flex-1">
        <div className="hidden min-h-0 flex-1 md:block">
          <WorkflowCanvas
            definition={definition}
            titleOf={titleOf}
            issueCounts={issueCounts}
            selectedId={selectedId}
            onSelect={setSelectedId}
            onInsert={(edge) => setAddTarget({ kind: 'edge', edge })}
            onAddAfter={(nodeId, when) => setAddTarget({ kind: 'after', nodeId, when })}
          />
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
        <aside className="hidden w-80 shrink-0 flex-col gap-4 overflow-auto border-l p-4 md:flex">
          <section className="flex flex-col gap-2">
            <h2 className="text-sm font-semibold">{t('editor.selectedTitle')}</h2>
            {selected ? (
              <div className="flex flex-col gap-2 text-sm">
                <span className="font-medium">{titleOf(selected.id).title}</span>
                <span className="text-muted-foreground">
                  {t('editor.nodeId')}: {selected.id}
                </span>
                {selected.type !== 'start' ? (
                  <Button
                    variant="outline"
                    size="sm"
                    onClick={() => {
                      change(removeNode(definition, selected.id))
                      setSelectedId(null)
                    }}
                  >
                    <Trash2Icon className="size-4" aria-hidden />
                    {t('editor.deleteNode')}
                  </Button>
                ) : null}
              </div>
            ) : (
              <p className="text-sm text-muted-foreground">{t('editor.selectHint')}</p>
            )}
          </section>
          <section className="flex flex-col gap-2">
            <h2 className="text-sm font-semibold">{t('editor.issuesTitle')}</h2>
            <IssuesList issues={issueList} onSelect={setSelectedId} />
          </section>
        </aside>
      </div>
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
        <span className="moldy-status-danger">{t('conflict')}</span>
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
        status === 'error' ? 'moldy-status-danger text-sm' : 'text-sm text-muted-foreground'
      }
      aria-live="polite"
    >
      {label}
    </span>
  )
}
