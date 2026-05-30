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
import { BUILDER_TOKENS as T } from './builder-tokens'
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
      <span
        className="inline-flex shrink-0 items-center justify-center"
        style={{
          width: 22,
          height: 22,
          borderRadius: 7,
          background: T.primaryBg,
          color: T.primary,
        }}
      >
        <FileTextIcon className="size-3" />
      </span>
      <span
        className="text-[13.5px] font-semibold"
        style={{ color: T.ink, letterSpacing: '-0.01em' }}
      >
        {title}
      </span>
    </PhaseCardHeader>
  )
}

function MintPill({ label, kind }: { label: string; kind: 'tool' | 'middleware' }) {
  // 도구 pill: 약한 surface + border. 미들웨어 pill: 민트 bg + 민트 ink.
  if (kind === 'tool') {
    return (
      <span
        className="font-mono text-[12px]"
        style={{
          padding: '2px 9px',
          borderRadius: 999,
          background: T.surfaceAlt,
          border: `1px solid ${T.border}`,
          color: T.ink2,
          letterSpacing: '-0.005em',
        }}
      >
        {label}
      </span>
    )
  }
  return (
    <span
      className="font-mono text-[12px]"
      style={{
        padding: '2px 9px',
        borderRadius: 999,
        background: T.primaryBg,
        color: T.primaryInk,
        letterSpacing: '-0.005em',
      }}
    >
      {label}
    </span>
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
    <div className="flex flex-col gap-3" style={{ padding: '16px 18px' }}>
      <div className="flex items-start gap-3">
        {image_url ? (
          // eslint-disable-next-line @next/next/no-img-element
          <img
            src={resolveImageUrl(image_url) ?? ''}
            alt="agent"
            className="size-16 shrink-0 rounded-lg object-contain"
            style={{ border: `1px solid ${T.border}` }}
          />
        ) : (
          <div
            className="flex size-16 shrink-0 items-center justify-center rounded-lg"
            style={{
              border: `1.5px dashed ${T.border}`,
              color: T.mutedSoft,
            }}
          >
            <BotIcon className="size-6" />
          </div>
        )}
        <div className="min-w-0 flex-1">
          <div
            className="text-[15px] font-semibold"
            style={{ color: T.ink, letterSpacing: '-0.01em' }}
          >
            {draft.name_ko || draft.name}
          </div>
          {draft.name && draft.name_ko !== draft.name && (
            <div className="text-[11.5px]" style={{ color: T.mutedSoft }}>
              {draft.name}
            </div>
          )}
          {draft.description && (
            <p
              className="mt-1 text-[13px]"
              style={{ color: T.ink2, lineHeight: 1.6, letterSpacing: '-0.005em' }}
            >
              {draft.description}
            </p>
          )}
        </div>
      </div>
      {tools.length > 0 && (
        <div>
          <div
            className="mb-1.5 flex items-center gap-1.5 text-[11.5px] font-semibold"
            style={{ color: T.muted, letterSpacing: '-0.005em' }}
          >
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
          <div
            className="mb-1.5 flex items-center gap-1.5 text-[11.5px] font-semibold"
            style={{ color: T.muted, letterSpacing: '-0.005em' }}
          >
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
    </div>
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
  const [focused, setFocused] = useState(false)
  const composingRef = useRef(false)
  return (
    <div style={{ padding: '12px 14px 4px' }}>
      <textarea
        value={value}
        onChange={(e) => onChange(e.target.value)}
        onFocus={() => setFocused(true)}
        onBlur={() => setFocused(false)}
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
        className="w-full resize-none font-sans text-[13.5px] outline-none transition-[border-color,box-shadow] duration-150"
        style={{
          padding: '10px 12px',
          background: T.surface,
          border: `1px solid ${focused ? T.primaryDim : T.border}`,
          borderRadius: 9,
          color: T.ink,
          lineHeight: 1.55,
          letterSpacing: '-0.005em',
          boxShadow: focused ? T.focusShadow : 'none',
        }}
      />
    </div>
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
    <div className="flex items-center justify-between" style={{ padding: '10px 14px' }}>
      <button
        type="button"
        onClick={() => setFeedbackOpen(!feedbackOpen)}
        disabled={disabled}
        className="inline-flex items-center gap-1.5 text-[12.5px] font-medium transition-colors disabled:cursor-not-allowed disabled:opacity-50"
        style={{ color: T.muted, background: 'transparent', borderRadius: 6 }}
      >
        <PencilIcon className="size-3" />
        {feedbackOpen ? t('closeFeedback') : t('writeFeedback')}
      </button>
      <div className="flex items-center gap-2">
        <button
          type="button"
          onClick={onRevision}
          disabled={disabled}
          className="inline-flex items-center gap-1.5 text-[12.5px] font-semibold transition-colors disabled:cursor-not-allowed disabled:opacity-50"
          style={{
            height: 32,
            padding: '0 13px',
            borderRadius: 9,
            background: T.surface,
            border: `1px solid ${T.border}`,
            color: T.ink2,
            letterSpacing: '-0.005em',
          }}
          onMouseEnter={(e) => {
            if (disabled) return
            e.currentTarget.style.background = T.surfaceAlt
          }}
          onMouseLeave={(e) => {
            e.currentTarget.style.background = T.surface
          }}
        >
          <XIcon className="size-3" strokeWidth={2.5} />
          {t('requestRevision')}
        </button>
        <button
          type="button"
          onClick={onApprove}
          disabled={disabled}
          className="inline-flex items-center gap-1.5 text-[12.5px] font-semibold text-white transition-colors active:translate-y-px disabled:cursor-not-allowed disabled:opacity-60"
          style={{
            height: 32,
            padding: '0 16px',
            borderRadius: 9,
            background: T.primary,
            boxShadow: T.primaryShadow,
            letterSpacing: '-0.005em',
          }}
          onMouseEnter={(e) => {
            if (disabled) return
            e.currentTarget.style.background = T.primaryHover
          }}
          onMouseLeave={(e) => {
            e.currentTarget.style.background = T.primary
          }}
        >
          <CheckIcon className="size-3" strokeWidth={3} />
          {t('finalApprove')}
        </button>
      </div>
    </div>
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
        {args.summary && (
          <div
            className="text-[13px]"
            style={{
              padding: '0 18px 14px',
              color: T.ink2,
              lineHeight: 1.6,
              letterSpacing: '-0.005em',
            }}
          >
            {args.summary}
          </div>
        )}
      </PhaseCard>
    </div>
  )
}

export const DraftApprovalToolUI = makeAssistantToolUI<DraftConfigArgs, unknown>({
  toolName: 'draft_approval',
  render: ({ args, status }) => <DraftApprovalView args={args} status={status.type} />,
})
