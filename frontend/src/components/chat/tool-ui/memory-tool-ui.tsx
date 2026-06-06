'use client'

import { useMemo, useState } from 'react'
import { makeAssistantToolUI } from '@assistant-ui/react'
import { BrainIcon, CheckIcon, Loader2Icon, PencilIcon, SaveIcon, XIcon } from 'lucide-react'
import { useTranslations } from 'next-intl'
import { toast } from 'sonner'

import { Button } from '@/components/ui/button'
import { Textarea } from '@/components/ui/textarea'
import {
  useApproveMemoryProposal,
  useEditAndApproveMemoryProposal,
  useMemoryProposal,
  useRejectMemoryProposal,
} from '@/lib/hooks/use-memory'
import type { MemoryEventPayload, MemoryEventType, MemoryProposal, MemoryScope } from '@/lib/types'
import { CollapsiblePill, pillStatusFromAssistantUi, type PillStatus } from './collapsible-pill'

interface MemoryToolArgs {
  scope?: MemoryScope
  content?: string
  reason?: string | null
}

interface MemoryToolResult extends MemoryEventPayload {
  memory_event?: MemoryEventType
}

function parseMemoryResult(result: unknown): MemoryToolResult | null {
  if (!result) return null
  if (typeof result === 'object' && !Array.isArray(result)) {
    return result as MemoryToolResult
  }
  if (typeof result !== 'string') return null
  try {
    const parsed = JSON.parse(result) as unknown
    if (parsed && typeof parsed === 'object' && !Array.isArray(parsed)) {
      return parsed as MemoryToolResult
    }
  } catch {
    return null
  }
  return null
}

function memoryResultFromArgs(args: MemoryToolArgs): MemoryToolResult {
  return {
    memory_event: undefined,
    scope: args.scope ?? 'user',
    content: args.content ?? '',
    reason: args.reason ?? null,
  }
}

export function memoryResultFromProposal(proposal: MemoryProposal): MemoryToolResult {
  return {
    memory_event:
      proposal.status === 'pending'
        ? 'memory_proposed'
        : proposal.status === 'approved'
          ? 'memory_saved'
          : 'memory_rejected',
    id: proposal.id,
    scope: proposal.scope,
    content: proposal.content,
    reason: proposal.reason,
    agent_id: proposal.agent_id,
    conversation_id: proposal.conversation_id,
    source_run_id: proposal.source_run_id,
  }
}

export function addMemoryToolResultIfSupported(
  addResult: ((result: unknown) => void) | undefined,
  result: unknown,
): boolean {
  if (!addResult) return false
  try {
    addResult(result)
    return true
  } catch {
    return false
  }
}

export function memoryReasonLabelKey(
  event: MemoryEventType | undefined,
): 'reason' | 'rejectedReason' {
  return event === 'memory_rejected' ? 'rejectedReason' : 'reason'
}

export function shouldMemoryToolDefaultExpand(
  event: MemoryEventType | undefined,
  statusType: string,
): boolean {
  if (statusType === 'running') return false
  return event === 'memory_proposed'
}

export function memoryToolPillStatus(
  event: MemoryEventType | undefined,
  statusType: string,
): PillStatus {
  if (event === 'memory_saved') return 'success'
  if (event === 'memory_rejected') return 'error'
  if (event === 'memory_proposed') return 'loading'
  return pillStatusFromAssistantUi(statusType)
}

function MemoryToolCard({
  args,
  result,
  statusType,
  addResult,
}: {
  args: MemoryToolArgs
  result?: unknown
  statusType: string
  addResult?: (result: unknown) => void
}) {
  const t = useTranslations('chat.memory')
  const approve = useApproveMemoryProposal()
  const reject = useRejectMemoryProposal()
  const editAndApprove = useEditAndApproveMemoryProposal()
  const parsed = useMemo(() => parseMemoryResult(result), [result])
  const proposalId = parsed?.memory_event === 'memory_proposed' && parsed.id ? parsed.id : undefined
  const proposal = useMemoryProposal(proposalId)
  const initial = parsed ?? memoryResultFromArgs(args)
  const serverResolved = useMemo(
    () => (proposal.data ? memoryResultFromProposal(proposal.data) : null),
    [proposal.data],
  )
  const [resolved, setResolved] = useState<MemoryToolResult | null>(null)
  const [editing, setEditing] = useState(false)
  const [draft, setDraft] = useState({
    sourceContent: initial.content,
    content: initial.content,
  })
  const view = resolved ?? serverResolved ?? initial
  const content = editing || draft.sourceContent === view.content ? draft.content : view.content
  const event = view.memory_event
  const pending = approve.isPending || reject.isPending || editAndApprove.isPending
  const isRunning = statusType === 'running' && !parsed
  const isProposal = event === 'memory_proposed'
  const canAct = isProposal && Boolean(view.id) && !pending

  async function handleApprove() {
    if (!view.id) return
    try {
      const response =
        content.trim() && content.trim() !== view.content
          ? await editAndApprove.mutateAsync({
              id: view.id,
              data: {
                content: content.trim(),
                reason: view.reason ?? null,
              },
            })
          : await approve.mutateAsync(view.id)
      const next: MemoryToolResult = {
        memory_event: 'memory_saved',
        id: response.memory.id,
        scope: response.memory.scope,
        content: response.memory.content,
        reason: response.memory.reason,
        agent_id: response.memory.agent_id,
        conversation_id: response.memory.source_conversation_id,
      }
      setDraft({ sourceContent: next.content, content: next.content })
      setResolved(next)
      setEditing(false)
      addMemoryToolResultIfSupported(addResult, next)
      toast.success(t('approveToast'))
    } catch (error) {
      toast.error(error instanceof Error ? error.message : t('actionFailed'))
    }
  }

  async function handleReject() {
    if (!view.id) return
    try {
      await reject.mutateAsync(view.id)
      const next: MemoryToolResult = {
        ...view,
        memory_event: 'memory_rejected',
      }
      setDraft({ sourceContent: next.content, content: next.content })
      setResolved(next)
      setEditing(false)
      addMemoryToolResultIfSupported(addResult, next)
      toast.success(t('rejectToast'))
    } catch (error) {
      toast.error(error instanceof Error ? error.message : t('actionFailed'))
    }
  }

  const heading =
    event === 'memory_saved'
      ? t('saved')
      : event === 'memory_rejected'
        ? t('rejected')
        : event === 'memory_proposed'
          ? t('proposed')
          : t('title')
  const description =
    event === 'memory_saved'
      ? t('savedDescription')
      : event === 'memory_rejected'
        ? t('rejectedDescription')
        : event === 'memory_proposed'
          ? t('proposalDescription')
          : t('running')
  const meta =
    isRunning || !view.content ? (
      description
    ) : (
      <span className="inline-block max-w-[min(34rem,54vw)] truncate">{view.content}</span>
    )
  const cardKey = `${event ?? statusType}-${view.id ?? view.content}`

  return (
    <div data-testid="memory-tool-card">
      <CollapsiblePill
        key={cardKey}
        kind="tool"
        leadingIcon={BrainIcon}
        status={memoryToolPillStatus(event, statusType)}
        title={heading}
        meta={meta}
        defaultExpanded={shouldMemoryToolDefaultExpand(event, statusType)}
        renderBody={
          isRunning
            ? undefined
            : () => (
                <div className="space-y-2">
                  <div className="space-y-2 rounded-lg border border-border/40 bg-background p-2.5">
                    {editing ? (
                      <Textarea
                        value={content}
                        onChange={(eventChange) =>
                          setDraft({
                            sourceContent: view.content,
                            content: eventChange.target.value,
                          })
                        }
                        className="min-h-20"
                        aria-label={t('contentLabel')}
                      />
                    ) : (
                      <p className="whitespace-pre-wrap text-sm leading-6 text-foreground">
                        {view.content || t('missingProposal')}
                      </p>
                    )}
                    {view.reason ? (
                      <p className="text-xs text-muted-foreground">
                        {t(memoryReasonLabelKey(event))}: {view.reason}
                      </p>
                    ) : null}
                  </div>

                  {isProposal ? (
                    <div className="flex flex-wrap justify-end gap-2">
                      {editing ? (
                        <Button
                          type="button"
                          variant="ghost"
                          size="sm"
                          disabled={pending}
                          data-testid="memory-proposal-cancel-edit"
                          onClick={() => {
                            setDraft({
                              sourceContent: view.content,
                              content: view.content,
                            })
                            setEditing(false)
                          }}
                        >
                          <XIcon className="size-4" aria-hidden />
                          {t('cancelEdit')}
                        </Button>
                      ) : (
                        <Button
                          type="button"
                          variant="ghost"
                          size="sm"
                          disabled={!canAct}
                          data-testid="memory-proposal-edit"
                          onClick={() => {
                            setDraft({
                              sourceContent: view.content,
                              content: view.content,
                            })
                            setEditing(true)
                          }}
                        >
                          <PencilIcon className="size-4" aria-hidden />
                          {t('edit')}
                        </Button>
                      )}
                      <Button
                        type="button"
                        variant="outline"
                        size="sm"
                        disabled={!canAct}
                        onClick={handleReject}
                        data-testid="memory-proposal-reject"
                      >
                        {reject.isPending ? (
                          <Loader2Icon className="size-4 animate-spin" aria-hidden />
                        ) : (
                          <XIcon className="size-4" aria-hidden />
                        )}
                        {t('reject')}
                      </Button>
                      <Button
                        type="button"
                        size="sm"
                        disabled={!canAct || content.trim().length === 0}
                        onClick={handleApprove}
                        data-testid={
                          editing ? 'memory-proposal-edit-approve' : 'memory-proposal-approve'
                        }
                      >
                        {approve.isPending || editAndApprove.isPending ? (
                          <Loader2Icon className="size-4 animate-spin" aria-hidden />
                        ) : editing ? (
                          <SaveIcon className="size-4" aria-hidden />
                        ) : (
                          <CheckIcon className="size-4" aria-hidden />
                        )}
                        {editing ? t('editAndApprove') : t('approve')}
                      </Button>
                    </div>
                  ) : null}
                </div>
              )
        }
      />
    </div>
  )
}

export const ProposeMemoryToolUI = makeAssistantToolUI<MemoryToolArgs, unknown>({
  toolName: 'propose_memory',
  render: ({ args, result, status, addResult }) => (
    <MemoryToolCard args={args} result={result} statusType={status.type} addResult={addResult} />
  ),
})

export const SaveUserMemoryToolUI = makeAssistantToolUI<MemoryToolArgs, unknown>({
  toolName: 'save_user_memory',
  render: ({ args, result, status, addResult }) => (
    <MemoryToolCard
      args={{ ...args, scope: 'user' }}
      result={result}
      statusType={status.type}
      addResult={addResult}
    />
  ),
})

export const SaveAgentMemoryToolUI = makeAssistantToolUI<MemoryToolArgs, unknown>({
  toolName: 'save_agent_memory',
  render: ({ args, result, status, addResult }) => (
    <MemoryToolCard
      args={{ ...args, scope: 'agent' }}
      result={result}
      statusType={status.type}
      addResult={addResult}
    />
  ),
})
