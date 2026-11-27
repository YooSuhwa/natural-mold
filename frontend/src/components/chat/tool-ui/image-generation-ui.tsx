'use client'

import { useLayoutEffect, useRef, useState } from 'react'
import type { ToolCallMessagePartProps } from '@assistant-ui/react'
import { useTranslations } from 'next-intl'
import { toast } from 'sonner'
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
import { canRespondToAction, useApprovalGeneration } from '@/lib/chat/hitl-readiness'
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

interface ImageDecisionTarget {
  hitl_interrupt_id?: string
  hitl_action_index?: number
}

function useImageDecisionKey(toolCallId: string, target: ImageDecisionTarget) {
  const hitl = useHiTL()
  const generation = useApprovalGeneration(hitl)
  return hitl?.canRespond !== undefined || hitl?.canRespondTo
    ? JSON.stringify([toolCallId, target.hitl_interrupt_id, target.hitl_action_index, generation])
    : toolCallId
}

function useImageDecision(complete: boolean, target: ImageDecisionTarget, initialPrompt: string) {
  const hitl = useHiTL()
  const t = useTranslations('chat.approval')
  const [submitted, setSubmitted] = useState(false)
  const [editPrompt, setPrompt] = useState(initialPrompt)
  const sending = useRef(false)
  const activeContext = useRef<HiTLContextValue | null>(null)
  const scoped = hitl?.canRespond !== undefined || Boolean(hitl?.canRespondTo)
  const ready =
    !complete && canRespondToAction(hitl, target) && (!scoped || Boolean(hitl?.registerDecision))

  useLayoutEffect(() => {
    activeContext.current = ready ? hitl : null
    return () => {
      activeContext.current = null
    }
  }, [hitl, ready])

  const canAct = () =>
    ready && activeContext.current === hitl && !sending.current && canRespondToAction(hitl, target)

  async function submit(payload: Record<string, unknown>, display: string) {
    if (!hitl || !canAct()) return
    const decision = toRespond(JSON.stringify(payload))
    let send: () => Promise<void>
    if (scoped) {
      const { registerDecision } = hitl
      const { hitl_action_index: index, hitl_interrupt_id: id } = target
      if (!registerDecision || index === undefined || !id) return
      send = () => registerDecision(index, decision, display, id)
    } else {
      send = () => hitl.onResumeDecisions([decision], display)
    }
    sending.current = true
    setSubmitted(true)
    try {
      await send()
    } catch {
      sending.current = false
      setSubmitted(false)
      if (activeContext.current === hitl) toast.error(t('resumeFailed'))
    }
  }

  return {
    finished: submitted || complete,
    disabled: submitted || !ready,
    submit,
    editPrompt,
    setEditPrompt: (value: string) => {
      if (canAct()) setPrompt(value)
    },
  }
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

interface ImageChoiceArgs extends ImageDecisionTarget {
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
  const { finished, disabled, submit, editPrompt, setEditPrompt } = useImageDecision(
    status === 'complete',
    args,
    args.auto_prompt ?? '',
  )
  const available = args.available !== false

  if (!available) {
    return <ImageChoiceUnavailable message={args.auto_prompt} />
  }

  const handle = async (choice: 'skip' | 'generate') => {
    const display = choice === 'skip' ? t('skip') : t('generate')
    await submit({ choice, prompt: choice === 'generate' ? editPrompt : undefined }, display)
  }

  const title = args.title || t('choiceTitle')

  return (
    <div className="my-3">
      <PhaseCard
        header={<ImageHeader phase={args.phase} title={title} subtitle={t('reviewRequired')} />}
        footer={
          finished ? null : (
            <PhaseCardFooter>
              <BuilderActionRow className="justify-end">
                <OutlineActionButton
                  onClick={() => void handle('skip')}
                  disabled={disabled}
                  label={t('skip')}
                  icon={<SkipForwardIcon className="size-3" strokeWidth={2.5} />}
                />
                <MintActionButton
                  onClick={() => void handle('generate')}
                  disabled={disabled}
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
            disabled={disabled}
          />
        </BuilderBody>
      </PhaseCard>
    </div>
  )
}

export function ImageChoiceToolUI({
  args,
  status,
  toolCallId,
}: ToolCallMessagePartProps<ImageChoiceArgs, unknown>) {
  return (
    <ImageChoice key={useImageDecisionKey(toolCallId, args)} args={args} status={status.type} />
  )
}

// ---------------------------------------------------------------------------
// Phase 6 2차: 미리보기 + 확정/재생성/skip
// ---------------------------------------------------------------------------

interface ImageApprovalArgs extends ImageDecisionTarget {
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
  const { finished, disabled, submit, editPrompt, setEditPrompt } = useImageDecision(
    status === 'complete',
    args,
    args.prompt ?? '',
  )

  const handle = async (choice: 'confirm' | 'regenerate' | 'skip') => {
    if (choice === 'confirm' && !args.image_url) return
    const display = { confirm: t('confirm'), regenerate: t('regenerate'), skip: t('skip') }[choice]
    await submit({ choice, prompt: editPrompt }, display)
  }

  const title = args.title || t('previewTitle')

  return (
    <div className="my-3">
      <PhaseCard
        header={<ImageHeader phase={args.phase} title={title} subtitle={t('reviewRequired')} />}
        footer={
          finished ? null : (
            <PhaseCardFooter>
              <BuilderActionRow className="flex-wrap justify-end">
                <OutlineActionButton
                  onClick={() => void handle('skip')}
                  disabled={disabled}
                  label={t('skip')}
                  icon={<SkipForwardIcon className="size-3" strokeWidth={2.5} />}
                />
                <OutlineActionButton
                  onClick={() => void handle('regenerate')}
                  disabled={disabled}
                  label={t('regenerate')}
                  icon={<RotateCwIcon className="size-3" strokeWidth={2.5} />}
                />
                <MintActionButton
                  onClick={() => void handle('confirm')}
                  disabled={disabled || !args.image_url}
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
          disabled={disabled}
        />
      </PhaseCard>
    </div>
  )
}

export function ImageApprovalToolUI({
  args,
  status,
  toolCallId,
}: ToolCallMessagePartProps<ImageApprovalArgs, unknown>) {
  return (
    <ImageApproval key={useImageDecisionKey(toolCallId, args)} args={args} status={status.type} />
  )
}
