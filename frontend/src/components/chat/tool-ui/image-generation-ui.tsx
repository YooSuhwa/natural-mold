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

function ImageChoice({ args, status }: { args: ImageChoiceArgs; status: 'running' | 'complete' | 'incomplete' | 'requires-action' }) {
  const hitl = useHiTL()
  const [submitted, setSubmitted] = useState<string | null>(null)
  const [editPrompt, setEditPrompt] = useState(args.auto_prompt ?? '')
  const isRunning = status !== 'complete'
  const available = args.available !== false

  if (!available) {
    return (
      <div className="my-3 rounded-xl border border-amber-200 bg-amber-50 px-4 py-3 text-sm dark:border-amber-900 dark:bg-amber-950">
        <div className="flex items-center gap-2 font-medium text-amber-700 dark:text-amber-300">
          <ImageIcon className="size-4" />
          이미지 생성 비활성화
        </div>
        <p className="mt-1 text-amber-700 dark:text-amber-300">
          {args.auto_prompt}
        </p>
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
    <div className="my-3 rounded-xl border border-zinc-200 bg-white shadow-sm dark:border-zinc-800 dark:bg-zinc-900">
      <div className="flex items-center gap-2 border-b border-zinc-200 px-4 py-3 text-sm font-semibold dark:border-zinc-800">
        <ImageIcon className="size-4 text-zinc-500" />
        {args.title || '에이전트 이미지를 생성하시겠습니까?'}
      </div>
      <div className="px-4 py-3">
        <label className="mb-1 block text-xs font-medium text-zinc-500">자동 생성 프롬프트 (편집 가능)</label>
        <textarea
          value={editPrompt}
          onChange={(e) => setEditPrompt(e.target.value)}
          rows={3}
          disabled={!!submitted || !isRunning}
          className="w-full resize-none rounded-lg border border-zinc-200 bg-zinc-50 px-3 py-2 text-xs text-zinc-700 dark:border-zinc-700 dark:bg-zinc-800 dark:text-zinc-300"
        />
        <div className="mt-3 flex justify-end gap-2">
          <button
            type="button"
            onClick={() => handle('skip')}
            disabled={!!submitted || !isRunning}
            className="inline-flex items-center gap-1.5 rounded-lg border border-zinc-200 px-3 py-1.5 text-sm font-medium text-zinc-700 transition hover:bg-zinc-50 disabled:cursor-not-allowed disabled:opacity-50 dark:border-zinc-700 dark:text-zinc-300 dark:hover:bg-zinc-800"
          >
            <SkipForwardIcon className="size-3.5" />
            넘어가기
          </button>
          <button
            type="button"
            onClick={() => handle('generate')}
            disabled={!!submitted || !isRunning}
            className="inline-flex items-center gap-1.5 rounded-lg bg-violet-600 px-3 py-1.5 text-sm font-medium text-white transition hover:bg-violet-700 disabled:cursor-not-allowed disabled:opacity-50"
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

function ImageApproval({ args, status }: { args: ImageApprovalArgs; status: 'running' | 'complete' | 'incomplete' | 'requires-action' }) {
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
    <div className="my-3 rounded-xl border border-zinc-200 bg-white shadow-sm dark:border-zinc-800 dark:bg-zinc-900">
      <div className="flex items-center gap-2 border-b border-zinc-200 px-4 py-3 text-sm font-semibold dark:border-zinc-800">
        <ImageIcon className="size-4 text-zinc-500" />
        {args.title || '이미지 미리보기'}
      </div>
      <div className="px-4 py-3">
        {args.error ? (
          <div className="rounded-lg border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-700 dark:border-red-900 dark:bg-red-950 dark:text-red-300">
            {args.error}
          </div>
        ) : args.image_url ? (
          // eslint-disable-next-line @next/next/no-img-element
          <img
            src={resolveImageUrl(args.image_url) ?? ''}
            alt="에이전트 이미지"
            className="mx-auto h-48 w-48 rounded-xl border border-zinc-200 object-contain dark:border-zinc-800"
          />
        ) : (
          <p className="text-sm text-zinc-500">이미지가 없습니다.</p>
        )}
        <div className="mt-3">
          <label className="mb-1 block text-xs font-medium text-zinc-500">재생성 프롬프트 (편집 가능)</label>
          <textarea
            value={editPrompt}
            onChange={(e) => setEditPrompt(e.target.value)}
            rows={3}
            disabled={!!submitted || !isRunning}
            className="w-full resize-none rounded-lg border border-zinc-200 bg-zinc-50 px-3 py-2 text-xs text-zinc-700 dark:border-zinc-700 dark:bg-zinc-800 dark:text-zinc-300"
          />
        </div>
        <div className="mt-3 flex flex-wrap justify-end gap-2">
          <button
            type="button"
            onClick={() => handle('skip')}
            disabled={!!submitted || !isRunning}
            className="inline-flex items-center gap-1.5 rounded-lg border border-zinc-200 px-3 py-1.5 text-sm font-medium text-zinc-700 transition hover:bg-zinc-50 disabled:cursor-not-allowed disabled:opacity-50 dark:border-zinc-700 dark:text-zinc-300 dark:hover:bg-zinc-800"
          >
            <SkipForwardIcon className="size-3.5" />
            넘어가기
          </button>
          <button
            type="button"
            onClick={() => handle('regenerate')}
            disabled={!!submitted || !isRunning}
            className="inline-flex items-center gap-1.5 rounded-lg border border-zinc-200 px-3 py-1.5 text-sm font-medium text-zinc-700 transition hover:bg-zinc-50 disabled:cursor-not-allowed disabled:opacity-50 dark:border-zinc-700 dark:text-zinc-300 dark:hover:bg-zinc-800"
          >
            <RotateCwIcon className="size-3.5" />
            재생성
          </button>
          <button
            type="button"
            onClick={() => handle('confirm')}
            disabled={!!submitted || !isRunning || !args.image_url}
            className={cn(
              'inline-flex items-center gap-1.5 rounded-lg bg-violet-600 px-3 py-1.5 text-sm font-medium text-white transition hover:bg-violet-700 disabled:cursor-not-allowed disabled:opacity-50',
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
