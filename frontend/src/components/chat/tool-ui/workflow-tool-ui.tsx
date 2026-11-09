'use client'

import Link from 'next/link'
import { ExternalLinkIcon, WorkflowIcon } from 'lucide-react'
import { useTranslations } from 'next-intl'
import { toast } from 'sonner'

import { useChatConversationId } from '@/components/chat/conversation-context'
import { Button } from '@/components/ui/button'
import { RunApprovalCard } from '@/features/workflows/components/run-approval-card'
import { RunStatusChip } from '@/features/workflows/components/run-status'
import {
  useCancelWorkflowRun,
  useToolCallWorkflowRun,
  useWorkflow,
} from '@/lib/hooks/use-workflows'
import { RUN_TERMINAL, type WorkflowRunDetail } from '@/lib/types/workflow'

/** Chat tools named ``workflow_<id>`` are published workflows (workflow-tool-plan 3.5). */
export function isWorkflowToolName(name: string): boolean {
  return name.startsWith('workflow_')
}

function parsedResult(result: unknown): { status?: string; error?: string } | null {
  const value = typeof result === 'string' ? safeJson(result) : result
  return value && typeof value === 'object' ? (value as { status?: string; error?: string }) : null
}

function safeJson(text: string): unknown {
  try {
    return JSON.parse(text)
  } catch {
    return null
  }
}

/**
 * A workflow the agent started from chat: its name, live status, the approval it waits on
 * (decided right here), a link to the run, and a cancel while it runs. The chat resumes on
 * its own once the run ends; cancelling ends the run, which resumes the chat as canceled.
 */
export function WorkflowToolCard({ toolCallId, result }: { toolCallId: string; result?: unknown }) {
  const t = useTranslations('workflows.chatTool')
  const conversationId = useChatConversationId()
  const run = useToolCallWorkflowRun(conversationId ?? undefined, toolCallId)
  if (run.data) return <RunCard run={run.data} />
  const answer = parsedResult(result)
  // No run: it could not start (unpublished, bad inputs, a changed agent) and said why.
  return (
    <div className="moldy-muted-panel flex items-center gap-2 px-3 py-2 text-sm">
      <WorkflowIcon className="size-4 shrink-0" aria-hidden />
      <span>{answer?.error ? t('notStarted', { reason: answer.error }) : t('starting')}</span>
    </div>
  )
}

function RunCard({ run }: { run: WorkflowRunDetail }) {
  const t = useTranslations('workflows.chatTool')
  const workflow = useWorkflow(run.workflow_id)
  const cancel = useCancelWorkflowRun(run.id)
  const finished = RUN_TERMINAL.includes(run.status)
  return (
    <div
      className="moldy-muted-panel flex flex-col gap-2 px-3 py-2"
      role="region"
      aria-label={t('label', { name: workflow.data?.name ?? '' })}
    >
      <div className="flex flex-wrap items-center gap-2 text-sm">
        <WorkflowIcon className="size-4 shrink-0" aria-hidden />
        <span className="font-medium">{workflow.data?.name ?? t('workflow')}</span>
        <RunStatusChip status={run.status} />
        <span className="flex-1" />
        <Link
          href={`/workflows/${run.workflow_id}/runs/${run.id}`}
          className="flex items-center gap-1 text-xs text-primary-strong underline"
        >
          <ExternalLinkIcon className="size-3" aria-hidden />
          {t('openRun')}
        </Link>
        {!finished ? (
          <Button
            size="sm"
            variant="outline"
            disabled={cancel.isPending || run.status === 'canceling'}
            onClick={() =>
              cancel.mutate(undefined, { onError: () => toast.error(t('cancelFailed')) })
            }
          >
            {t('cancel')}
          </Button>
        ) : null}
      </div>
      {!finished ? <p className="text-xs text-muted-foreground">{t('waitingHint')}</p> : null}
      <RunApprovalCard run={run} />
      {finished && run.output ? (
        <pre className="moldy-muted-panel max-h-40 overflow-auto p-2 font-mono text-xs whitespace-pre-wrap">
          {JSON.stringify(run.output, null, 2)}
        </pre>
      ) : null}
      {finished && run.error_message ? (
        <p className="text-xs text-status-danger">{run.error_message}</p>
      ) : null}
    </div>
  )
}
