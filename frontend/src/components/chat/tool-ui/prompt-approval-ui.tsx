'use client'

import { makeAssistantToolUI } from '@assistant-ui/react'
import { FileTextIcon } from 'lucide-react'
import { useApprovalForm } from './use-approval-form'
import { ApprovalFooter } from './approval-footer'

interface PromptApprovalArgs {
  phase: number
  title: string
  system_prompt: string
  summary?: string
}

function PromptApproval({
  args,
  status,
}: {
  args: PromptApprovalArgs
  status: 'running' | 'complete' | 'incomplete' | 'requires-action'
}) {
  const form = useApprovalForm({
    isComplete: status === 'complete',
    revisionFallback: '프롬프트를 다시 작성해주세요',
  })

  const prompt = args.system_prompt ?? ''

  return (
    <div className="my-3 rounded-xl border border-border bg-card shadow-sm">
      <div className="flex items-center gap-2 border-b border-border px-4 py-3 text-sm font-semibold">
        <FileTextIcon className="size-4 text-muted-foreground" />
        {args.title || '시스템 프롬프트'}
      </div>
      <div className="px-4 py-3">
        <pre className="max-h-96 overflow-y-auto whitespace-pre-wrap rounded-lg bg-muted p-3 text-xs text-foreground">
          {prompt}
        </pre>
      </div>
      {args.summary && (
        <div className="border-t border-border px-4 py-3 text-sm text-foreground">
          {args.summary}
        </div>
      )}
      <ApprovalFooter form={form} />
    </div>
  )
}

export const PromptApprovalToolUI = makeAssistantToolUI<PromptApprovalArgs, unknown>({
  toolName: 'prompt_approval',
  render: ({ args, status }) => <PromptApproval args={args} status={status.type} />,
})
