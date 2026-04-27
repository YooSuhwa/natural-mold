'use client'

import { useState } from 'react'
import { makeAssistantToolUI } from '@assistant-ui/react'
import { CheckIcon, WrenchIcon, XIcon, BlocksIcon } from 'lucide-react'
import { cn } from '@/lib/utils'
import { useHiTL } from '@/lib/chat/hitl-context'

type ItemKind = 'tool' | 'middleware'

interface ToolItem {
  tool_name?: string
  middleware_name?: string
  description?: string
  reason?: string
  path?: string
}

interface RecommendationArgs {
  phase: number
  title: string
  items: ToolItem[]
  summary?: string
  item_kind: ItemKind
}

function getItemName(item: ToolItem, kind: ItemKind): string {
  if (kind === 'tool') return item.tool_name ?? ''
  return item.middleware_name ?? ''
}

function getItemPath(item: ToolItem, kind: ItemKind): string {
  if (item.path) return item.path
  const name = getItemName(item, kind)
  return kind === 'tool' ? `tools/${name}.yaml` : `middlewares/${name}.yaml`
}

function ItemCard({ item, kind }: { item: ToolItem; kind: ItemKind }) {
  const name = getItemName(item, kind)
  const Icon = kind === 'middleware' ? BlocksIcon : WrenchIcon
  return (
    <div className="rounded-lg border border-zinc-200 p-3 dark:border-zinc-800">
      <div className="flex items-start gap-2">
        <Icon className="mt-0.5 size-4 text-zinc-500" />
        <div className="flex-1">
          <div className="font-mono text-sm font-semibold">{name}</div>
          {(item.path || name) && (
            <div className="mt-1 text-xs text-zinc-500">
              경로: <code className="rounded bg-zinc-100 px-1 py-0.5 dark:bg-zinc-800">{getItemPath(item, kind)}</code>
            </div>
          )}
          {item.reason && (
            <p className="mt-2 text-sm text-zinc-700 dark:text-zinc-300">사유: {item.reason}</p>
          )}
        </div>
      </div>
    </div>
  )
}

function RecommendationApproval({ args, status }: { args: RecommendationArgs; status: 'running' | 'complete' | 'incomplete' | 'requires-action' }) {
  const hitl = useHiTL()
  const [revision, setRevision] = useState('')
  const [submitted, setSubmitted] = useState<'approved' | 'revision' | null>(null)

  const items = args.items ?? []
  const isRunning = status !== 'complete'

  const handleApprove = async () => {
    if (submitted) return
    setSubmitted('approved')
    await hitl?.onResume({ approved: true }, '승인')
  }

  const handleRevision = async () => {
    if (submitted) return
    const msg = revision.trim() || '수정 요청'
    setSubmitted('revision')
    await hitl?.onResume({ approved: false, revision_message: msg }, msg)
  }

  return (
    <div className="my-3 rounded-xl border border-zinc-200 bg-white shadow-sm dark:border-zinc-800 dark:bg-zinc-900">
      <div className="border-b border-zinc-200 px-4 py-3 text-sm font-semibold dark:border-zinc-800">
        {args.title || '추천 결과'}
      </div>
      <div className="space-y-2 px-4 py-3">
        {items.map((item, idx) => (
          <ItemCard key={idx} item={item} kind={args.item_kind ?? 'tool'} />
        ))}
        {items.length === 0 && (
          <p className="text-sm text-zinc-500">추천된 항목이 없습니다.</p>
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
        {submitted && (
          <p className="mt-2 text-xs text-zinc-500">
            {submitted === 'approved' ? '승인되었습니다. 다음 단계로 진행합니다...' : '수정 요청을 전달했습니다...'}
          </p>
        )}
      </div>
    </div>
  )
}

export const RecommendationApprovalToolUI = makeAssistantToolUI<RecommendationArgs, unknown>({
  toolName: 'recommendation_approval',
  render: ({ args, status }) => <RecommendationApproval args={args} status={status.type} />,
})
