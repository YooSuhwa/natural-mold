'use client'

import { useState } from 'react'
import { makeAssistantToolUI } from '@assistant-ui/react'
import { ImageIcon, RotateCwIcon, SparklesIcon, SkipForwardIcon, CheckIcon } from 'lucide-react'
import { cn, resolveImageUrl } from '@/lib/utils'
import { useHiTL } from '@/lib/chat/hitl-context'

// ---------------------------------------------------------------------------
// Phase 6 1차: skip / generate 선택
// ---------------------------------------------------------------------------

interface ImageChoiceArgs {
  phase: number
  title?: string
  auto_prompt?: string
  available?: boolean
  options?: Array<{ value: string; label: string }>
}

function ImageChoice({
  args,
  status,
}: {
  args: ImageChoiceArgs
  status: 'running' | 'complete' | 'incomplete' | 'requires-action'
}) {
  const hitl = useHiTL()
  const [submitted, setSubmitted] = useState<string | null>(null)
  const [editPrompt, setEditPrompt] = useState(args.auto_prompt ?? '')
  const isRunning = status !== 'complete'
  const available = args.available !== false

  if (!available) {
    return (
      <div className="my-3 rounded-xl border border-status-warn/30 bg-status-warn/10 px-4 py-3 text-sm">
        <div className="flex items-center gap-2 font-medium text-status-warn">
          <ImageIcon className="size-4" />
          이미지 생성 비활성화
        </div>
        <p className="mt-1 text-status-warn">{args.auto_prompt}</p>
      </div>
    )
  }

  const handle = async (choice: 'skip' | 'generate') => {
    if (submitted) return
    setSubmitted(choice)
    const display = choice === 'skip' ? '넘어가기' : '생성하기'
    await hitl?.onResume(
      { choice, prompt: choice === 'generate' ? editPrompt : undefined },
      display,
    )
  }

  return (
    <div className="my-3 rounded-xl border border-border bg-card shadow-sm">
      <div className="flex items-center gap-2 border-b border-border px-4 py-3 text-sm font-semibold">
        <ImageIcon className="size-4 text-muted-foreground" />
        {args.title || '에이전트 이미지를 생성하시겠습니까?'}
      </div>
      <div className="px-4 py-3">
        <label className="mb-1 block text-xs font-medium text-muted-foreground">
          자동 생성 프롬프트 (편집 가능)
        </label>
        <textarea
          value={editPrompt}
          onChange={(e) => setEditPrompt(e.target.value)}
          rows={3}
          disabled={!!submitted || !isRunning}
          className="w-full resize-none rounded-lg border border-border bg-muted px-3 py-2 text-xs text-foreground focus:outline-none focus:ring-2 focus:ring-ring"
        />
        <div className="mt-3 flex justify-end gap-2">
          <button
            type="button"
            onClick={() => handle('skip')}
            disabled={!!submitted || !isRunning}
            className="inline-flex items-center gap-1.5 rounded-lg border border-border px-3 py-1.5 text-sm font-medium text-foreground transition hover:bg-muted disabled:cursor-not-allowed disabled:opacity-50"
          >
            <SkipForwardIcon className="size-3.5" />
            넘어가기
          </button>
          <button
            type="button"
            onClick={() => handle('generate')}
            disabled={!!submitted || !isRunning}
            className="inline-flex items-center gap-1.5 rounded-lg bg-primary-strong px-3 py-1.5 text-sm font-medium text-white transition hover:opacity-90 disabled:cursor-not-allowed disabled:opacity-50"
          >
            <SparklesIcon className="size-3.5" />
            생성하기
          </button>
        </div>
      </div>
    </div>
  )
}

export const ImageChoiceToolUI = makeAssistantToolUI<ImageChoiceArgs, unknown>({
  toolName: 'image_choice',
  render: ({ args, status }) => <ImageChoice args={args} status={status.type} />,
})

// ---------------------------------------------------------------------------
// Phase 6 2차: 미리보기 + 확정/재생성/skip
// ---------------------------------------------------------------------------

interface ImageApprovalArgs {
  phase: number
  title?: string
  image_url?: string | null
  prompt?: string
  error?: string
  options?: Array<{ value: string; label: string }>
}

function ImageApproval({
  args,
  status,
}: {
  args: ImageApprovalArgs
  status: 'running' | 'complete' | 'incomplete' | 'requires-action'
}) {
  const hitl = useHiTL()
  const [submitted, setSubmitted] = useState<string | null>(null)
  const [editPrompt, setEditPrompt] = useState(args.prompt ?? '')
  const isRunning = status !== 'complete'

  const handle = async (choice: 'confirm' | 'regenerate' | 'skip') => {
    if (submitted) return
    setSubmitted(choice)
    const display = { confirm: '확정', regenerate: '재생성', skip: '넘어가기' }[choice]
    await hitl?.onResume({ choice, prompt: editPrompt }, display)
  }

  return (
    <div className="my-3 rounded-xl border border-border bg-card shadow-sm">
      <div className="flex items-center gap-2 border-b border-border px-4 py-3 text-sm font-semibold">
        <ImageIcon className="size-4 text-muted-foreground" />
        {args.title || '이미지 미리보기'}
      </div>
      <div className="px-4 py-3">
        {args.error ? (
          <div className="rounded-lg border border-status-danger/30 bg-status-danger/10 px-3 py-2 text-sm text-status-danger">
            {args.error}
          </div>
        ) : args.image_url ? (
          // eslint-disable-next-line @next/next/no-img-element
          <img
            src={resolveImageUrl(args.image_url) ?? ''}
            alt="에이전트 이미지"
            className="mx-auto h-48 w-48 rounded-xl border border-border object-contain"
          />
        ) : (
          <p className="text-sm text-muted-foreground">이미지가 없습니다.</p>
        )}
        <div className="mt-3">
          <label className="mb-1 block text-xs font-medium text-muted-foreground">
            재생성 프롬프트 (편집 가능)
          </label>
          <textarea
            value={editPrompt}
            onChange={(e) => setEditPrompt(e.target.value)}
            rows={3}
            disabled={!!submitted || !isRunning}
            className="w-full resize-none rounded-lg border border-border bg-muted px-3 py-2 text-xs text-foreground focus:outline-none focus:ring-2 focus:ring-ring"
          />
        </div>
        <div className="mt-3 flex flex-wrap justify-end gap-2">
          <button
            type="button"
            onClick={() => handle('skip')}
            disabled={!!submitted || !isRunning}
            className="inline-flex items-center gap-1.5 rounded-lg border border-border px-3 py-1.5 text-sm font-medium text-foreground transition hover:bg-muted disabled:cursor-not-allowed disabled:opacity-50"
          >
            <SkipForwardIcon className="size-3.5" />
            넘어가기
          </button>
          <button
            type="button"
            onClick={() => handle('regenerate')}
            disabled={!!submitted || !isRunning}
            className="inline-flex items-center gap-1.5 rounded-lg border border-border px-3 py-1.5 text-sm font-medium text-foreground transition hover:bg-muted disabled:cursor-not-allowed disabled:opacity-50"
          >
            <RotateCwIcon className="size-3.5" />
            재생성
          </button>
          <button
            type="button"
            onClick={() => handle('confirm')}
            disabled={!!submitted || !isRunning || !args.image_url}
            className={cn(
              'inline-flex items-center gap-1.5 rounded-lg bg-primary-strong px-3 py-1.5 text-sm font-medium text-white transition hover:opacity-90 disabled:cursor-not-allowed disabled:opacity-50',
              submitted === 'confirm' && 'opacity-60',
            )}
          >
            <CheckIcon className="size-3.5" />
            확정
          </button>
        </div>
      </div>
    </div>
  )
}

export const ImageApprovalToolUI = makeAssistantToolUI<ImageApprovalArgs, unknown>({
  toolName: 'image_approval',
  render: ({ args, status }) => <ImageApproval args={args} status={status.type} />,
})
