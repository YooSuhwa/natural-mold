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
import { MintActionButton, OutlineActionButton } from './builder-form-controls'
import {
  BuilderActionRow,
  BuilderBody,
  BuilderHeaderIcon,
  BuilderMuted,
  BuilderPhaseLabel,
  BuilderTextarea,
  BuilderTitle,
} from './builder-primitives'
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
      <BuilderHeaderIcon>
        <ImageIcon className="size-3" />
      </BuilderHeaderIcon>
      <BuilderTitle>{title}</BuilderTitle>
      {subtitle && <BuilderMuted>· {subtitle}</BuilderMuted>}
      <div className="flex-1" />
      <BuilderPhaseLabel>PHASE {phase}</BuilderPhaseLabel>
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
  const composingRef = useRef(false)
  return (
    <div className="flex flex-col gap-1.5">
      <label className="moldy-ui-caption-plus font-semibold moldy-builder-color-muted">
        {label}
      </label>
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
        rows={rows}
        disabled={disabled}
        className="bg-[var(--builder-surface-alt)] moldy-ui-compact moldy-builder-color-ink-2 disabled:cursor-not-allowed"
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
    <div className="moldy-chat-card my-3 flex items-start gap-2 px-3.5 py-3">
      <AlertTriangleIcon className="size-4 shrink-0 moldy-builder-color-muted" />
      <div>
        <div className="moldy-ui-compact font-semibold moldy-builder-color-ink">
          {t('disabled')}
        </div>
        {message && (
          <p className="mt-1 moldy-ui-compact leading-relaxed moldy-builder-color-ink-2">
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
              <BuilderActionRow className="justify-end">
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
              </BuilderActionRow>
            </PhaseCardFooter>
          )
        }
      >
        <BuilderBody loose>
          <PromptEditor
            label={t('autoPrompt')}
            value={editPrompt}
            onChange={setEditPrompt}
            disabled={isLocked}
          />
        </BuilderBody>
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
    <BuilderBody loose className="flex flex-col gap-3">
      {error ? (
        <div className="moldy-status-surface moldy-status-danger flex items-start gap-2 rounded-lg px-3 py-2.5 moldy-ui-compact leading-normal">
          <AlertTriangleIcon className="mt-0.5 size-3.5 shrink-0" />
          <span>{error}</span>
        </div>
      ) : imageUrl ? (
        // eslint-disable-next-line @next/next/no-img-element
        <img
          src={resolveImageUrl(imageUrl) ?? ''}
          alt={t('agentImageAlt')}
          className="moldy-card mx-auto size-48 object-contain"
        />
      ) : (
        <p className="moldy-ui-body-sm moldy-builder-color-muted-soft">{t('noImage')}</p>
      )}
      <PromptEditor
        label={t('regeneratePrompt')}
        value={prompt}
        onChange={setPrompt}
        disabled={disabled}
      />
    </BuilderBody>
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
              <BuilderActionRow className="flex-wrap justify-end">
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
              </BuilderActionRow>
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
