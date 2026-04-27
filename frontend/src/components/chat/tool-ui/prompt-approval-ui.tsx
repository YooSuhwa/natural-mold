'use client'

import { useState } from 'react'
import { makeAssistantToolUI } from '@assistant-ui/react'
import { CheckIcon, FileTextIcon, XIcon } from 'lucide-react'
import { cn } from '@/lib/utils'
import { useHiTL } from '@/lib/chat/hitl-context'

interface PromptApprovalArgs {
  phase: number
  title: string
  system_prompt: string
  summary?: string
}

function PromptApproval({ args, status }: { args: PromptApprovalArgs; status: 'running' | 'complete' | 'incomplete' | 'requires-action' }) {
  const hitl = useHiTL()
  const [revision, setRevision] = useState('')
  const [submitted, setSubmitted] = useState<'approved' | 'revision' | null>(null)
  const [expanded, setExpanded] = useState(false)
  const isRunning = status !== 'complete'

  const handleApprove = async () => {
    if (submitted) return
    setSubmitted('approved')
    await hitl?.onResume({ approved: true }, '승인')
  }
  const handleRevision = async () => {
    if (submitted) return
    const msg = revision.trim() || '프롬프트를 다시 작성해주세요'
    setSubmitted('revision')
    await hitl?.onResume({ approved: false, revision_message: msg }, msg)
  }

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
      <div className="border-t border-zinc-200 px-4 py-3 dark:border-zinc-800">
        <textarea
          value={revision}
          onChange={(e) => setRevision(e.target.value)}
          placeholder="수정 의견을 입력하세요..."
          disabled={!!submitted || !isRunning}
          rows={3}
          className="w-full resize-none rounded-lg border border-zinc-200 bg-zinc-50 px-3 py-2 text-sm focus:border-zinc-400 focus:outline-none focus:ring-1 focus:ring-zinc-400 dark:border-zinc-700 dark:bg-zinc-800"
        />
        <div className="mt-3 flex justify-end gap-2">
          <button
            type="button"
            onClick={handleRevision}
            disabled={!!submitted || !isRunning}
            className={cn(
              'inline-flex items-center gap-1.5 rounded-lg border border-zinc-200 px-3 py-1.5 text-sm font-medium text-zinc-700 transition hover:bg-zinc-50 disabled:cursor-not-allowed disabled:opacity-50 dark:border-zinc-700 dark:text-zinc-300 dark:hover:bg-zinc-800',
              submitted === 'revision' && 'bg-zinc-50 dark:bg-zinc-800',
            )}
          >
            <XIcon className="size-3.5" />
            수정요청
          </button>
          <button
            type="button"
            onClick={handleApprove}
            disabled={!!submitted || !isRunning}
            className={cn(
              'inline-flex items-center gap-1.5 rounded-lg bg-zinc-900 px-3 py-1.5 text-sm font-medium text-white transition hover:bg-zinc-800 disabled:cursor-not-allowed disabled:opacity-50 dark:bg-white dark:text-zinc-900 dark:hover:bg-zinc-100',
              submitted === 'approved' && 'opacity-60',
            )}
          >
            <CheckIcon className="size-3.5" />
            승인
          </button>
        </div>
      </div>
    </div>
  )
}

export const PromptApprovalToolUI = makeAssistantToolUI<PromptApprovalArgs, unknown>({
  toolName: 'prompt_approval',
  render: ({ args, status }) => <PromptApproval args={args} status={status.type} />,
})
