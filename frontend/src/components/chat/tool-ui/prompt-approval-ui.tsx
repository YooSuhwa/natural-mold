'use client'

import { useState } from 'react'
import { makeAssistantToolUI } from '@assistant-ui/react'
import { useTranslations } from 'next-intl'
import { CheckIcon, FileTextIcon, PencilIcon, XIcon } from 'lucide-react'
import {
  BuilderFeedbackTextarea,
  MintActionButton,
  OutlineActionButton,
} from './builder-form-controls'
import {
  BuilderActionRow,
  BuilderBody,
  BuilderButton,
  BuilderHeaderIcon,
  BuilderMuted,
  BuilderPhaseLabel,
  BuilderSummary,
  BuilderTitle,
} from './builder-primitives'
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
      <BuilderHeaderIcon>
        <FileTextIcon className="size-3" />
      </BuilderHeaderIcon>
      <BuilderTitle>{title}</BuilderTitle>
      <BuilderMuted>{t('reviewRequiredPrefix')}</BuilderMuted>
      <div className="flex-1" />
      <BuilderPhaseLabel>PHASE {phase}</BuilderPhaseLabel>
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
              <BuilderActionRow>
                <BuilderButton
                  tone="ghost"
                  onClick={() => setFeedbackOpen(!feedbackOpen)}
                  disabled={form.isLocked}
                >
                  <PencilIcon className="size-3" />
                  {feedbackOpen ? t('closeFeedback') : t('writeFeedback')}
                </BuilderButton>
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
              </BuilderActionRow>
            </PhaseCardFooter>
          )
        }
      >
        <BuilderBody loose>
          <pre className="moldy-builder-pre">{prompt}</pre>
        </BuilderBody>
        {args.summary && <BuilderSummary>{args.summary}</BuilderSummary>}
      </PhaseCard>
    </div>
  )
}

export const PromptApprovalToolUI = makeAssistantToolUI<PromptApprovalArgs, unknown>({
  toolName: 'prompt_approval',
  render: ({ args, status }) => <PromptApproval args={args} status={status.type} />,
})
