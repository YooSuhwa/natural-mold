'use client'

import { useState } from 'react'
import { makeAssistantToolUI } from '@assistant-ui/react'
import { useTranslations } from 'next-intl'
import { CheckIcon, FileTextIcon, PencilIcon, XIcon } from 'lucide-react'
import { BUILDER_TOKENS as T } from './builder-tokens'
import {
  BuilderFeedbackTextarea,
  MintActionButton,
  OutlineActionButton,
} from './builder-form-controls'
import { PhaseCard, PhaseCardFooter, PhaseCardHeader } from './phase-card'
import { useApprovalForm } from './use-approval-form'

interface PromptApprovalArgs {
  phase: number
  title: string
  system_prompt: string
  summary?: string
}

function PromptApprovalHeader({ phase, title }: { phase: number; title: string }) {
  const t = useTranslations('chat.builderApproval')
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
        <FileTextIcon className="size-3" />
      </span>
      <span
        className="text-[13.5px] font-semibold"
        style={{ color: T.ink, letterSpacing: '-0.01em' }}
      >
        {title}
      </span>
      <span className="text-[12px]" style={{ color: T.muted }}>
        {t('reviewRequiredPrefix')}
      </span>
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

function PromptApproval({
  args,
  status,
}: {
  args: PromptApprovalArgs
  status: 'running' | 'complete' | 'incomplete' | 'requires-action'
}) {
  const t = useTranslations('chat.builderApproval')
  const form = useApprovalForm({
    isComplete: status === 'complete',
    revisionFallback: t('promptRevisionFallback'),
  })
  const [feedbackOpen, setFeedbackOpen] = useState(false)
  const prompt = args.system_prompt ?? ''
  const title = args.title || t('systemPrompt')

  return (
    <div className="my-3">
      <PhaseCard
        header={<PromptApprovalHeader phase={args.phase} title={title} />}
        footer={
          form.isLocked ? null : (
            <PhaseCardFooter>
              {feedbackOpen && (
                <BuilderFeedbackTextarea
                  value={form.revision}
                  onChange={form.setRevision}
                  disabled={form.isLocked}
                  placeholder={t('promptFeedbackPlaceholder')}
                />
              )}
              <div className="flex items-center justify-between" style={{ padding: '10px 14px' }}>
                <button
                  type="button"
                  onClick={() => setFeedbackOpen(!feedbackOpen)}
                  disabled={form.isLocked}
                  className="inline-flex items-center gap-1.5 text-[12.5px] font-medium transition-colors disabled:cursor-not-allowed disabled:opacity-50"
                  style={{ color: T.muted, background: 'transparent', borderRadius: 6 }}
                >
                  <PencilIcon className="size-3" />
                  {feedbackOpen ? t('closeFeedback') : t('writeFeedback')}
                </button>
                <div className="flex items-center gap-2">
                  <OutlineActionButton
                    onClick={form.handleRevision}
                    disabled={form.isLocked}
                    label={t('requestRevision')}
                    icon={<XIcon className="size-3" strokeWidth={2.5} />}
                  />
                  <MintActionButton
                    onClick={form.handleApprove}
                    disabled={form.isLocked}
                    label={t('approveAndContinue')}
                    icon={<CheckIcon className="size-3" strokeWidth={3} />}
                  />
                </div>
              </div>
            </PhaseCardFooter>
          )
        }
      >
        <div style={{ padding: '14px 18px 16px' }}>
          <pre
            className="max-h-96 overflow-y-auto whitespace-pre-wrap font-mono text-[12.5px]"
            style={{
              padding: '12px 14px',
              borderRadius: 10,
              background: T.surfaceAlt,
              border: `1px solid ${T.border}`,
              color: T.ink2,
              lineHeight: 1.6,
              letterSpacing: '-0.005em',
            }}
          >
            {prompt}
          </pre>
          {args.summary && (
            <p
              className="mt-3 text-[13px]"
              style={{
                color: T.ink2,
                lineHeight: 1.6,
                letterSpacing: '-0.005em',
              }}
            >
              {args.summary}
            </p>
          )}
        </div>
      </PhaseCard>
    </div>
  )
}

export const PromptApprovalToolUI = makeAssistantToolUI<PromptApprovalArgs, unknown>({
  toolName: 'prompt_approval',
  render: ({ args, status }) => <PromptApproval args={args} status={status.type} />,
})
