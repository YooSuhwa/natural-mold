'use client'

import { useRef, useState } from 'react'
import { makeAssistantToolUI } from '@assistant-ui/react'
import { useTranslations } from 'next-intl'
import {
  BlocksIcon,
  BotIcon,
  CheckIcon,
  FileTextIcon,
  PencilIcon,
  WrenchIcon,
  XIcon,
} from 'lucide-react'
import { resolveImageUrl } from '@/lib/utils'
import {
  BuilderActionRow,
  BuilderBody,
  BuilderButton,
  BuilderFeedbackWrap,
  BuilderHeaderIcon,
  BuilderPill,
  BuilderSummary,
  BuilderTextarea,
  BuilderTitle,
} from './builder-primitives'
import { PhaseCard, PhaseCardFooter, PhaseCardHeader } from './phase-card'
import { useApprovalForm } from './use-approval-form'

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
// Shared pieces — header + summary (mint tokens, builder-card 일관성)
// ---------------------------------------------------------------------------

function DraftHeader({ title, variant }: { title: string; variant: 'plain' | 'gradient' }) {
  return (
    <PhaseCardHeader variant={variant}>
      <BuilderHeaderIcon>
        <FileTextIcon className="size-3" />
      </BuilderHeaderIcon>
      <BuilderTitle>{title}</BuilderTitle>
    </PhaseCardHeader>
  )
}

function MintPill({ label, kind }: { label: string; kind: 'tool' | 'middleware' }) {
  return (
    <BuilderPill tone={kind === 'middleware' ? 'primary' : 'default'}>
      {label}
    </BuilderPill>
  )
}

function DraftConfigSummary({
  draft,
  image_url,
}: {
  draft: DraftConfig
  image_url?: string | null
}) {
  const t = useTranslations('chat.builderApproval')
  const tools = draft.tools ?? []
  const middlewares = draft.middlewares ?? []
  return (
    <BuilderBody loose className="flex flex-col gap-3">
      <div className="flex items-start gap-3">
        {image_url ? (
          // eslint-disable-next-line @next/next/no-img-element
          <img
            src={resolveImageUrl(image_url) ?? ''}
            alt="agent"
            className="size-16 shrink-0 rounded-lg object-contain"
            style={{ border: '1px solid var(--builder-border)' }}
          />
        ) : (
          <div className="flex size-16 shrink-0 items-center justify-center rounded-lg border border-dashed border-[var(--builder-border)] text-[var(--builder-muted-soft)]">
            <BotIcon className="size-6" />
          </div>
        )}
        <div className="min-w-0 flex-1">
          <div className="moldy-ui-card-title font-semibold text-[var(--builder-ink)]">
            {draft.name_ko || draft.name}
          </div>
          {draft.name && draft.name_ko !== draft.name && (
            <div className="moldy-ui-caption-plus text-[var(--builder-muted-soft)]">
              {draft.name}
            </div>
          )}
          {draft.description && (
            <p className="moldy-builder-copy mt-1">
              {draft.description}
            </p>
          )}
        </div>
      </div>
      {tools.length > 0 && (
        <div>
          <div className="mb-1.5 flex items-center gap-1.5 moldy-ui-caption-plus font-semibold text-[var(--builder-muted)]">
            <WrenchIcon className="size-3" />
            {t('draftTools', { count: tools.length })}
          </div>
          <div className="flex flex-wrap gap-1.5">
            {tools.map((tool) => (
              <MintPill key={tool} label={tool} kind="tool" />
            ))}
          </div>
        </div>
      )}
      {middlewares.length > 0 && (
        <div>
          <div className="mb-1.5 flex items-center gap-1.5 moldy-ui-caption-plus font-semibold text-[var(--builder-muted)]">
            <BlocksIcon className="size-3" />
            {t('draftMiddlewares', { count: middlewares.length })}
          </div>
          <div className="flex flex-wrap gap-1.5">
            {middlewares.map((mw) => (
              <MintPill key={mw} label={mw} kind="middleware" />
            ))}
          </div>
        </div>
      )}
    </BuilderBody>
  )
}

// ---------------------------------------------------------------------------
// Phase 7 — DraftConfig 표시 (자동 진행, 입력 폼 없음)
// ---------------------------------------------------------------------------

function DraftConfigCardView({ args }: { args: DraftConfigArgs }) {
  const t = useTranslations('chat.builderApproval')
  const draft = args.draft || {}
  const image = args.image_url ?? draft.image_url ?? null
  return (
    <div className="my-3">
      <PhaseCard
        header={<DraftHeader title={args.title || t('draftPreviewTitle')} variant="plain" />}
      >
        <DraftConfigSummary draft={draft} image_url={image} />
      </PhaseCard>
    </div>
  )
}

export const DraftConfigCardToolUI = makeAssistantToolUI<DraftConfigArgs, unknown>({
  toolName: 'draft_config_card',
  render: ({ args }) => <DraftConfigCardView args={args} />,
})

// ---------------------------------------------------------------------------
// Phase 8 — 최종 승인 (interrupt approval) — 민트 button + frozen footer
// ---------------------------------------------------------------------------

function FeedbackTextarea({
  value,
  onChange,
  disabled,
}: {
  value: string
  onChange: (v: string) => void
  disabled: boolean
}) {
  const t = useTranslations('chat.builderApproval')
  const composingRef = useRef(false)
  return (
    <BuilderFeedbackWrap>
      <BuilderTextarea
        value={value}
        onChange={(e) => onChange(e.target.value)}
        onCompositionStart={() => {
          composingRef.current = true
        }}
        onCompositionEnd={() => {
          composingRef.current = false
        }}
        onKeyDown={(e) => {
          if (e.key === 'Enter' && composingRef.current) e.stopPropagation()
        }}
        placeholder={t('placeholderWithExample')}
        rows={2}
        disabled={disabled}
      />
    </BuilderFeedbackWrap>
  )
}

function DraftApprovalActions({
  onApprove,
  onRevision,
  disabled,
  feedbackOpen,
  setFeedbackOpen,
}: {
  onApprove: () => void
  onRevision: () => void
  disabled: boolean
  feedbackOpen: boolean
  setFeedbackOpen: (v: boolean) => void
}) {
  const t = useTranslations('chat.builderApproval')
  return (
    <BuilderActionRow>
      <BuilderButton
        tone="ghost"
        onClick={() => setFeedbackOpen(!feedbackOpen)}
        disabled={disabled}
      >
        <PencilIcon className="size-3" />
        {feedbackOpen ? t('closeFeedback') : t('writeFeedback')}
      </BuilderButton>
      <div className="flex items-center gap-2">
        <BuilderButton tone="secondary" onClick={onRevision} disabled={disabled}>
          <XIcon className="size-3" strokeWidth={2.5} />
          {t('requestRevision')}
        </BuilderButton>
        <BuilderButton tone="primary" onClick={onApprove} disabled={disabled} className="px-4">
          <CheckIcon className="size-3" strokeWidth={3} />
          {t('finalApprove')}
        </BuilderButton>
      </div>
    </BuilderActionRow>
  )
}

function DraftApprovalView({
  args,
  status,
}: {
  args: DraftConfigArgs
  status: 'running' | 'complete' | 'incomplete' | 'requires-action'
}) {
  const t = useTranslations('chat.builderApproval')
  const form = useApprovalForm({
    isComplete: status === 'complete',
    approveDisplay: t('finalApproveLabel'),
  })
  const [feedbackOpen, setFeedbackOpen] = useState(false)
  const draft = args.draft || {}
  const image = args.image_url ?? draft.image_url ?? null

  return (
    <div className="my-3">
      <PhaseCard
        header={<DraftHeader title={args.title || t('finalConfirmTitle')} variant="gradient" />}
        footer={
          // 결정 끝나면 footer(수정 요청 / 최종 승인 + textarea) 전체 unmount.
          form.isLocked ? null : (
            <PhaseCardFooter>
              {feedbackOpen && (
                <FeedbackTextarea
                  value={form.revision}
                  onChange={form.setRevision}
                  disabled={form.isLocked}
                />
              )}
              <DraftApprovalActions
                onApprove={form.handleApprove}
                onRevision={form.handleRevision}
                disabled={form.isLocked}
                feedbackOpen={feedbackOpen}
                setFeedbackOpen={setFeedbackOpen}
              />
            </PhaseCardFooter>
          )
        }
      >
        <DraftConfigSummary draft={draft} image_url={image} />
        {args.summary && <BuilderSummary>{args.summary}</BuilderSummary>}
      </PhaseCard>
    </div>
  )
}

export const DraftApprovalToolUI = makeAssistantToolUI<DraftConfigArgs, unknown>({
  toolName: 'draft_approval',
  render: ({ args, status }) => <DraftApprovalView args={args} status={status.type} />,
})
