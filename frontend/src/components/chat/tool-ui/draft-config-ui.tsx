'use client'

import { useState } from 'react'
import { makeAssistantToolUI } from '@assistant-ui/react'
import { CheckIcon, FileTextIcon, XIcon, BlocksIcon, WrenchIcon, BotIcon } from 'lucide-react'
import { cn, resolveImageUrl } from '@/lib/utils'
import { useHiTL } from '@/lib/chat/hitl-context'

interface DraftConfig {
  name?: string
  name_ko?: string
  description?: string
  system_prompt?: string
  tools?: string[]
  middlewares?: string[]
  primary_task_type?: string
  use_cases?: string[]
  image_url?: string | null
}

interface DraftConfigArgs {
  phase: number
  title?: string
  draft: DraftConfig
  image_url?: string | null
  summary?: string
}

// ---------------------------------------------------------------------------
// Phase 7 — DraftConfig 표시 (자동 진행, 입력 폼 없음)
// ---------------------------------------------------------------------------

function DraftConfigSummary({ draft, image_url }: { draft: DraftConfig; image_url?: string | null }) {
  const tools = draft.tools ?? []
  const middlewares = draft.middlewares ?? []
  return (
    <div className="space-y-3">
      <div className="flex items-start gap-3">
        {image_url ? (
          // eslint-disable-next-line @next/next/no-img-element
          <img
            src={resolveImageUrl(image_url) ?? ''}
            alt="agent"
            className="size-16 flex-shrink-0 rounded-lg border border-zinc-200 object-contain dark:border-zinc-800"
          />
        ) : (
          <div className="flex size-16 flex-shrink-0 items-center justify-center rounded-lg border border-dashed border-zinc-300 text-zinc-400 dark:border-zinc-700">
            <BotIcon className="size-6" />
          </div>
        )}
        <div className="flex-1">
          <div className="font-semibold">{draft.name_ko || draft.name}</div>
          {draft.name && draft.name_ko !== draft.name && (
            <div className="text-xs text-zinc-500">{draft.name}</div>
          )}
          {draft.description && (
            <p className="mt-1 text-sm text-zinc-700 dark:text-zinc-300">{draft.description}</p>
          )}
        </div>
      </div>
      {tools.length > 0 && (
        <div>
          <div className="mb-1 flex items-center gap-1 text-xs font-medium text-zinc-500">
            <WrenchIcon className="size-3.5" />
            도구 ({tools.length})
          </div>
          <div className="flex flex-wrap gap-1.5">
            {tools.map((t) => (
              <span
                key={t}
                className="rounded-full bg-zinc-100 px-2.5 py-0.5 font-mono text-xs dark:bg-zinc-800"
              >
                {t}
              </span>
            ))}
          </div>
        </div>
      )}
      {middlewares.length > 0 && (
        <div>
          <div className="mb-1 flex items-center gap-1 text-xs font-medium text-zinc-500">
            <BlocksIcon className="size-3.5" />
            미들웨어 ({middlewares.length})
          </div>
          <div className="flex flex-wrap gap-1.5">
            {middlewares.map((m) => (
              <span
                key={m}
                className="rounded-full bg-violet-100 px-2.5 py-0.5 font-mono text-xs text-violet-700 dark:bg-violet-950 dark:text-violet-300"
              >
                {m}
              </span>
            ))}
          </div>
        </div>
      )}
    </div>
  )
}

function DraftConfigCardView({ args }: { args: DraftConfigArgs }) {
  const draft = args.draft || {}
  const image = args.image_url ?? draft.image_url ?? null
  return (
    <div className="my-3 rounded-xl border border-zinc-200 bg-white shadow-sm dark:border-zinc-800 dark:bg-zinc-900">
      <div className="flex items-center gap-2 border-b border-zinc-200 px-4 py-3 text-sm font-semibold dark:border-zinc-800">
        <FileTextIcon className="size-4 text-zinc-500" />
        {args.title || '에이전트 설정 미리보기'}
      </div>
      <div className="px-4 py-3">
        <DraftConfigSummary draft={draft} image_url={image} />
      </div>
    </div>
  )
}

export const DraftConfigCardToolUI = makeAssistantToolUI<DraftConfigArgs, unknown>({
  toolName: 'draft_config_card',
  render: ({ args }) => <DraftConfigCardView args={args} />,
})

// ---------------------------------------------------------------------------
// Phase 8 — 최종 승인 (interrupt approval)
// ---------------------------------------------------------------------------

function DraftApprovalView({ args, status }: { args: DraftConfigArgs; status: 'running' | 'complete' | 'incomplete' | 'requires-action' }) {
  const hitl = useHiTL()
  const [revision, setRevision] = useState('')
  const [submitted, setSubmitted] = useState<'approved' | 'revision' | null>(null)
  const isRunning = status !== 'complete'
  const draft = args.draft || {}
  const image = args.image_url ?? draft.image_url ?? null

  const handleApprove = async () => {
    if (submitted) return
    setSubmitted('approved')
    await hitl?.onResume({ approved: true }, '최종 승인')
  }
  const handleRevision = async () => {
    if (submitted) return
    const msg = revision.trim() || '수정 요청'
    setSubmitted('revision')
    await hitl?.onResume({ approved: false, revision_message: msg }, msg)
  }

  return (
    <div className="my-3 rounded-xl border-2 border-violet-200 bg-white shadow-sm dark:border-violet-800 dark:bg-zinc-900">
      <div className="flex items-center gap-2 border-b border-violet-200 bg-violet-50/50 px-4 py-3 text-sm font-semibold dark:border-violet-800 dark:bg-violet-950/30">
        <FileTextIcon className="size-4 text-violet-600 dark:text-violet-400" />
        {args.title || '최종 확인'}
      </div>
      <div className="px-4 py-3">
        <DraftConfigSummary draft={draft} image_url={image} />
      </div>
      {args.summary && (
        <div className="border-t border-violet-200 px-4 py-3 text-sm text-zinc-700 dark:border-violet-800 dark:text-zinc-300">
          {args.summary}
        </div>
      )}
      <div className="border-t border-violet-200 px-4 py-3 dark:border-violet-800">
        <textarea
          value={revision}
          onChange={(e) => setRevision(e.target.value)}
          placeholder="수정 의견을 입력하세요 (예: 도구를 X로 바꿔줘)..."
          disabled={!!submitted || !isRunning}
          rows={2}
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
              'inline-flex items-center gap-1.5 rounded-lg bg-violet-600 px-3 py-1.5 text-sm font-medium text-white transition hover:bg-violet-700 disabled:cursor-not-allowed disabled:opacity-50',
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

export const DraftApprovalToolUI = makeAssistantToolUI<DraftConfigArgs, unknown>({
  toolName: 'draft_approval',
  render: ({ args, status }) => <DraftApprovalView args={args} status={status.type} />,
})
