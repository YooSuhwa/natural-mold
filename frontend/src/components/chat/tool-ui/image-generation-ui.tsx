'use client'

import { useRef, useState } from 'react'
import { makeAssistantToolUI } from '@assistant-ui/react'
import { useTranslations } from 'next-intl'
import {
  AlertTriangleIcon,
  CheckIcon,
  ImageIcon,
  RotateCwIcon,
  SkipForwardIcon,
  SparklesIcon,
} from 'lucide-react'
import { resolveImageUrl } from '@/lib/utils'
import { toRespond } from '@/lib/chat/decision-mappers'
import { useHiTL, type HiTLContextValue } from '@/lib/chat/hitl-context'
import { BUILDER_TOKENS as T } from './builder-tokens'
import { MintActionButton, OutlineActionButton } from './builder-form-controls'
import { PhaseCard, PhaseCardFooter, PhaseCardHeader } from './phase-card'

async function submitChoice(
  hitl: HiTLContextValue | null,
  payload: Record<string, unknown>,
  display: string,
) {
  await hitl?.onResumeDecisions([toRespond(JSON.stringify(payload))], display)
}

function ImageHeader({
  phase,
  title,
  subtitle,
}: {
  phase: number
  title: string
  subtitle?: string
}) {
  return (
    <PhaseCardHeader>
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
        <ImageIcon className="size-3" />
      </span>
      <span
        className="text-[13.5px] font-semibold"
        style={{ color: T.ink, letterSpacing: '-0.01em' }}
      >
        {title}
      </span>
      {subtitle && (
        <span className="text-[12px]" style={{ color: T.muted }}>
          · {subtitle}
        </span>
      )}
      <div className="flex-1" />
      <span
        className="text-[10.5px] font-semibold uppercase"
        style={{ color: T.primaryInk, letterSpacing: '0.04em' }}
      >
        PHASE {phase}
      </span>
    </PhaseCardHeader>
  )
}

/** IME-guarded prompt editor textarea (Builder mint focus ring). */
function PromptEditor({
  label,
  value,
  onChange,
  disabled,
  rows = 3,
}: {
  label: string
  value: string
  onChange: (v: string) => void
  disabled: boolean
  rows?: number
}) {
  const [focused, setFocused] = useState(false)
  const composingRef = useRef(false)
  return (
    <div className="flex flex-col gap-1.5">
      <label
        className="text-[11.5px] font-semibold"
        style={{ color: T.muted, letterSpacing: '-0.005em' }}
      >
        {label}
      </label>
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
        rows={rows}
        disabled={disabled}
        className="w-full resize-none font-sans text-[12.5px] outline-none transition-[border-color,box-shadow] duration-150 disabled:cursor-not-allowed"
        style={{
          padding: '10px 12px',
          background: T.surfaceAlt,
          border: `1px solid ${focused ? T.primaryDim : T.border}`,
          borderRadius: 9,
          color: T.ink2,
          lineHeight: 1.55,
          letterSpacing: '-0.005em',
          boxShadow: focused ? T.focusShadow : 'none',
        }}
      />
    </div>
  )
}

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

function ImageChoiceUnavailable({ message }: { message?: string }) {
  const t = useTranslations('chat.imageGeneration')
  return (
    <div
      className="my-3 flex items-start gap-2"
      style={{
        padding: '12px 14px',
        borderRadius: 12,
        background: T.surfaceAlt,
        border: `1px solid ${T.border}`,
      }}
    >
      <AlertTriangleIcon className="size-4 shrink-0" style={{ color: T.muted }} />
      <div>
        <div
          className="text-[12.5px] font-semibold"
          style={{ color: T.ink, letterSpacing: '-0.005em' }}
        >
          {t('disabled')}
        </div>
        {message && (
          <p
            className="mt-1 text-[12.5px]"
            style={{ color: T.ink2, lineHeight: 1.6, letterSpacing: '-0.005em' }}
          >
            {message}
          </p>
        )}
      </div>
    </div>
  )
}

function ImageChoice({
  args,
  status,
}: {
  args: ImageChoiceArgs
  status: 'running' | 'complete' | 'incomplete' | 'requires-action'
}) {
  const t = useTranslations('chat.imageGeneration')
  const hitl = useHiTL()
  const [submitted, setSubmitted] = useState<string | null>(null)
  const [editPrompt, setEditPrompt] = useState(args.auto_prompt ?? '')
  const isRunning = status !== 'complete'
  const isLocked = !!submitted || !isRunning
  const available = args.available !== false

  if (!available) {
    return <ImageChoiceUnavailable message={args.auto_prompt} />
  }

  const handle = async (choice: 'skip' | 'generate') => {
    if (submitted) return
    setSubmitted(choice)
    const display = choice === 'skip' ? t('skip') : t('generate')
    await submitChoice(
      hitl,
      { choice, prompt: choice === 'generate' ? editPrompt : undefined },
      display,
    )
  }

  const title = args.title || t('choiceTitle')

  return (
    <div className="my-3">
      <PhaseCard
        header={<ImageHeader phase={args.phase} title={title} subtitle={t('reviewRequired')} />}
        footer={
          isLocked ? null : (
            <PhaseCardFooter>
              <div className="flex items-center justify-end gap-2" style={{ padding: '10px 14px' }}>
                <OutlineActionButton
                  onClick={() => void handle('skip')}
                  disabled={isLocked}
                  label={t('skip')}
                  icon={<SkipForwardIcon className="size-3" strokeWidth={2.5} />}
                />
                <MintActionButton
                  onClick={() => void handle('generate')}
                  disabled={isLocked}
                  label={t('generate')}
                  icon={<SparklesIcon className="size-3" strokeWidth={2.5} />}
                />
              </div>
            </PhaseCardFooter>
          )
        }
      >
        <div style={{ padding: '14px 18px 16px' }}>
          <PromptEditor
            label={t('autoPrompt')}
            value={editPrompt}
            onChange={setEditPrompt}
            disabled={isLocked}
          />
        </div>
      </PhaseCard>
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

function ImageApprovalBody({
  imageUrl,
  error,
  prompt,
  setPrompt,
  disabled,
}: {
  imageUrl: string | null
  error?: string
  prompt: string
  setPrompt: (v: string) => void
  disabled: boolean
}) {
  const t = useTranslations('chat.imageGeneration')
  return (
    <div className="flex flex-col gap-3" style={{ padding: '14px 18px 16px' }}>
      {error ? (
        <div
          className="flex items-start gap-2 text-[12.5px]"
          style={{
            padding: '10px 12px',
            borderRadius: 10,
            background: 'oklch(0.97 0.05 27)',
            border: `1px solid oklch(0.85 0.1 27)`,
            color: 'oklch(0.42 0.15 27)',
            lineHeight: 1.5,
          }}
        >
          <AlertTriangleIcon className="mt-0.5 size-3.5 shrink-0" />
          <span>{error}</span>
        </div>
      ) : imageUrl ? (
        // eslint-disable-next-line @next/next/no-img-element
        <img
          src={resolveImageUrl(imageUrl) ?? ''}
          alt={t('agentImageAlt')}
          className="mx-auto size-48 rounded-[14px] object-contain"
          style={{ border: `1px solid ${T.border}`, background: T.surfaceAlt }}
        />
      ) : (
        <p className="text-[13px]" style={{ color: T.mutedSoft }}>
          {t('noImage')}
        </p>
      )}
      <PromptEditor
        label={t('regeneratePrompt')}
        value={prompt}
        onChange={setPrompt}
        disabled={disabled}
      />
    </div>
  )
}

function ImageApproval({
  args,
  status,
}: {
  args: ImageApprovalArgs
  status: 'running' | 'complete' | 'incomplete' | 'requires-action'
}) {
  const t = useTranslations('chat.imageGeneration')
  const hitl = useHiTL()
  const [submitted, setSubmitted] = useState<string | null>(null)
  const [editPrompt, setEditPrompt] = useState(args.prompt ?? '')
  const isRunning = status !== 'complete'
  const isLocked = !!submitted || !isRunning

  const handle = async (choice: 'confirm' | 'regenerate' | 'skip') => {
    if (submitted) return
    setSubmitted(choice)
    const display = { confirm: t('confirm'), regenerate: t('regenerate'), skip: t('skip') }[choice]
    await submitChoice(hitl, { choice, prompt: editPrompt }, display)
  }

  const title = args.title || t('previewTitle')

  return (
    <div className="my-3">
      <PhaseCard
        header={<ImageHeader phase={args.phase} title={title} subtitle={t('reviewRequired')} />}
        footer={
          isLocked ? null : (
            <PhaseCardFooter>
              <div
                className="flex flex-wrap items-center justify-end gap-2"
                style={{ padding: '10px 14px' }}
              >
                <OutlineActionButton
                  onClick={() => void handle('skip')}
                  disabled={isLocked}
                  label={t('skip')}
                  icon={<SkipForwardIcon className="size-3" strokeWidth={2.5} />}
                />
                <OutlineActionButton
                  onClick={() => void handle('regenerate')}
                  disabled={isLocked}
                  label={t('regenerate')}
                  icon={<RotateCwIcon className="size-3" strokeWidth={2.5} />}
                />
                <MintActionButton
                  onClick={() => void handle('confirm')}
                  disabled={isLocked || !args.image_url}
                  label={t('confirm')}
                  icon={<CheckIcon className="size-3" strokeWidth={3} />}
                />
              </div>
            </PhaseCardFooter>
          )
        }
      >
        <ImageApprovalBody
          imageUrl={args.image_url ?? null}
          error={args.error}
          prompt={editPrompt}
          setPrompt={setEditPrompt}
          disabled={isLocked}
        />
      </PhaseCard>
    </div>
  )
}

export const ImageApprovalToolUI = makeAssistantToolUI<ImageApprovalArgs, unknown>({
  toolName: 'image_approval',
  render: ({ args, status }) => <ImageApproval args={args} status={status.type} />,
})
