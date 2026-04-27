'use client'

import { useState } from 'react'
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

function PromptApproval({ args, status }: { args: PromptApprovalArgs; status: 'running' | 'complete' | 'incomplete' | 'requires-action' }) {
  const form = useApprovalForm({
    isComplete: status === 'complete',
    revisionFallback: '프롬프트를 다시 작성해주세요',
  })
  const [expanded, setExpanded] = useState(false)

  const prompt = args.system_prompt ?? ''
  const truncated = prompt.length > 400 && !expanded
  const display = truncated ? prompt.slice(0, 400) + '...' : prompt

  return (
    <div className="my-3 rounded-xl border border-zinc-200 bg-white shadow-sm dark:border-zinc-800 dark:bg-zinc-900">
      <div className="flex items-center gap-2 border-b border-zinc-200 px-4 py-3 text-sm font-semibold dark:border-zinc-800">
        <FileTextIcon className="size-4 text-zinc-500" />
        {args.title || '시스템 프롬프트'}
      </div>
      <div className="px-4 py-3">
        <pre className="whitespace-pre-wrap rounded-lg bg-zinc-50 p-3 text-xs text-zinc-700 dark:bg-zinc-800 dark:text-zinc-300">
          {display}
        </pre>
        {truncated && (
          <button
            type="button"
            onClick={() => setExpanded(true)}
            className="mt-2 text-xs text-blue-600 hover:underline dark:text-blue-400"
          >
            전체 보기
          </button>
        )}
      </div>
      {args.summary && (
        <div className="border-t border-zinc-200 px-4 py-3 text-sm text-zinc-700 dark:border-zinc-800 dark:text-zinc-300">
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
