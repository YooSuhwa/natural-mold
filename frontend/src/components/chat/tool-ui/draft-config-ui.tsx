'use client'

import { makeAssistantToolUI } from '@assistant-ui/react'
import { FileTextIcon, BlocksIcon, WrenchIcon, BotIcon } from 'lucide-react'
import { resolveImageUrl } from '@/lib/utils'
import { useApprovalForm } from './use-approval-form'
import { ApprovalFooter } from './approval-footer'

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

function DraftConfigSummary({
  draft,
  image_url,
}: {
  draft: DraftConfig
  image_url?: string | null
}) {
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
            className="size-16 flex-shrink-0 rounded-lg border border-border object-contain"
          />
        ) : (
          <div className="flex size-16 flex-shrink-0 items-center justify-center rounded-lg border border-dashed border-border text-muted-foreground">
            <BotIcon className="size-6" />
          </div>
        )}
        <div className="flex-1">
          <div className="font-semibold">{draft.name_ko || draft.name}</div>
          {draft.name && draft.name_ko !== draft.name && (
            <div className="text-xs text-muted-foreground">{draft.name}</div>
          )}
          {draft.description && (
            <p className="mt-1 text-sm text-foreground">{draft.description}</p>
          )}
        </div>
      </div>
      {tools.length > 0 && (
        <div>
          <div className="mb-1 flex items-center gap-1 text-xs font-medium text-muted-foreground">
            <WrenchIcon className="size-3.5" />
            도구 ({tools.length})
          </div>
          <div className="flex flex-wrap gap-1.5">
            {tools.map((t) => (
              <span
                key={t}
                className="rounded-full bg-muted px-2.5 py-0.5 font-mono text-xs"
              >
                {t}
              </span>
            ))}
          </div>
        </div>
      )}
      {middlewares.length > 0 && (
        <div>
          <div className="mb-1 flex items-center gap-1 text-xs font-medium text-muted-foreground">
            <BlocksIcon className="size-3.5" />
            미들웨어 ({middlewares.length})
          </div>
          <div className="flex flex-wrap gap-1.5">
            {middlewares.map((m) => (
              <span
                key={m}
                className="rounded-full bg-status-accent/15 px-2.5 py-0.5 font-mono text-xs text-status-accent"
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
    <div className="my-3 rounded-xl border border-border bg-card shadow-sm">
      <div className="flex items-center gap-2 border-b border-border px-4 py-3 text-sm font-semibold">
        <FileTextIcon className="size-4 text-muted-foreground" />
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

function DraftApprovalView({
  args,
  status,
}: {
  args: DraftConfigArgs
  status: 'running' | 'complete' | 'incomplete' | 'requires-action'
}) {
  const form = useApprovalForm({
    isComplete: status === 'complete',
    approveDisplay: '최종 승인',
  })
  const draft = args.draft || {}
  const image = args.image_url ?? draft.image_url ?? null

  return (
    <div className="my-3 rounded-xl border-2 border-status-accent/30 bg-card shadow-sm">
      <div className="flex items-center gap-2 border-b border-status-accent/30 bg-status-accent/5 px-4 py-3 text-sm font-semibold">
        <FileTextIcon className="size-4 text-status-accent" />
        {args.title || '최종 확인'}
      </div>
      <div className="px-4 py-3">
        <DraftConfigSummary draft={draft} image_url={image} />
      </div>
      {args.summary && (
        <div className="border-t border-status-accent/30 px-4 py-3 text-sm text-foreground">
          {args.summary}
        </div>
      )}
      <ApprovalFooter
        form={form}
        accent="violet"
        placeholder="수정 의견을 입력하세요 (예: 도구를 X로 바꿔줘)..."
        rows={2}
      />
    </div>
  )
}

export const DraftApprovalToolUI = makeAssistantToolUI<DraftConfigArgs, unknown>({
  toolName: 'draft_approval',
  render: ({ args, status }) => <DraftApprovalView args={args} status={status.type} />,
})
