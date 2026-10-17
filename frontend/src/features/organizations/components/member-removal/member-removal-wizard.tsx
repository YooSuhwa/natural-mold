'use client'

import { useState } from 'react'
import { useTranslations } from 'next-intl'
import { toast } from 'sonner'

import { ApiErrorState } from '@/components/shared/api-error-state'
import { DialogShell } from '@/components/shared/dialog-shell'
import { TransferOwnershipDialog } from '@/components/sharing/transfer-ownership-dialog'
import { Button } from '@/components/ui/button'
import { Skeleton } from '@/components/ui/skeleton'
import { useMemberExitPreview, useRemoveMemberWithSettlement } from '@/lib/hooks/use-member-exit'
import { cn } from '@/lib/utils'
import { useRemovalErrorMessage } from './removal-error'
import {
  buildResolutions,
  planRemoval,
  rowKey,
  type ActionableResource,
  type RowChoice,
} from './removal-plan'
import { RemovalResourcesStep } from './removal-resources-step'
import { RemovalConfirmStep, RemovalEffectsStep } from './removal-summary-steps'

export interface RemovalTarget {
  userId: string
  displayName: string
}

interface MemberRemovalWizardProps {
  orgId: string
  orgName: string
  member: RemovalTarget | null
  /** Members page version (`DirectoryPage.updated_at`), echoed as `If-Match`. */
  version: string
  onClose: () => void
}

const STEPS = [1, 2, 3] as const
type Step = (typeof STEPS)[number]

/** S15 member removal wizard over the E-02 exit-preview and removal APIs. */
export function MemberRemovalWizard({ member, onClose, ...rest }: MemberRemovalWizardProps) {
  const t = useTranslations('orgAdmin.memberRemoval')
  return (
    <DialogShell
      open={member !== null}
      onOpenChange={(open) => (open ? undefined : onClose())}
      size="lg"
      height="auto"
    >
      <DialogShell.Header
        title={member ? t('title', { name: member.displayName, org: rest.orgName }) : ''}
        description={t('description')}
      />
      {member ? (
        <WizardBody key={member.userId} member={member} onClose={onClose} {...rest} />
      ) : null}
    </DialogShell>
  )
}

function WizardBody({
  orgId,
  member,
  version,
  onClose,
}: Omit<MemberRemovalWizardProps, 'member'> & { member: RemovalTarget }) {
  const t = useTranslations('orgAdmin.memberRemoval')
  const errorMessage = useRemovalErrorMessage()
  const preview = useMemberExitPreview(orgId, member.userId)
  const remove = useRemoveMemberWithSettlement(orgId, member.userId)
  const [step, setStep] = useState<Step>(1)
  const [choices, setChoices] = useState<Map<string, RowChoice>>(() => new Map())
  const [reason, setReason] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [transferring, setTransferring] = useState<ActionableResource | null>(null)

  const plan = preview.data ? planRemoval(preview.data) : null
  const resolutions = plan ? buildResolutions(plan, choices) : null
  const canProceed = resolutions !== null
  const canSubmit = canProceed && reason.trim().length > 0 && !remove.isPending

  function choose(key: string, choice: RowChoice) {
    setChoices((current) => new Map(current).set(key, choice))
  }

  function applyOwner(owner: { id: string; displayName: string }) {
    if (!plan) return
    setChoices((current) => {
      const next = new Map(current)
      for (const row of plan.rows) {
        if (row.kind !== 'actionable' || !row.canTransfer) continue
        const key = rowKey(row.resource)
        if (next.get(key)?.action === 'delete') continue
        next.set(key, { action: 'transfer', newOwner: owner })
      }
      return next
    })
  }

  function submit() {
    if (!resolutions) return
    setError(null)
    remove.mutate(
      { data: { reason: reason.trim(), resolutions }, version },
      {
        onSuccess: () => {
          toast.success(t('done', { name: member.displayName }))
          onClose()
        },
        onError: (err) => {
          setError(errorMessage(err))
          // The server state moved (stale version, late share): the settled mutation
          // re-reads the preview (org-admin subtree), so start again from step 1.
          setStep(1)
        },
      },
    )
  }

  return (
    <>
      <DialogShell.Body>
        {preview.isLoading ? (
          <Skeleton className="h-40 w-full" />
        ) : !preview.data || !plan ? (
          <ApiErrorState error={preview.error} onRetry={() => void preview.refetch()} />
        ) : (
          <div className="space-y-4">
            <StepIndicator step={step} />
            {error ? (
              <p role="alert" className="text-sm text-status-danger">
                {error}
              </p>
            ) : null}
            {preview.data.is_last_admin ? (
              <p role="alert" className="text-sm text-status-danger">
                {t('lastAdmin')}
              </p>
            ) : plan.blocked ? (
              <p role="alert" className="text-sm text-status-danger">
                {t('blockedNotice')}
              </p>
            ) : null}
            {step === 1 ? (
              <RemovalResourcesStep
                rows={plan.rows}
                leaverId={member.userId}
                choices={choices}
                onChoose={choose}
                onApplyOwner={applyOwner}
                onTransferNow={setTransferring}
              />
            ) : step === 2 ? (
              <RemovalEffectsStep preview={preview.data} />
            ) : (
              <RemovalConfirmStep
                name={member.displayName}
                transfers={resolutions?.filter((r) => r.action === 'transfer').length ?? 0}
                deletes={resolutions?.filter((r) => r.action === 'delete').length ?? 0}
                reason={reason}
                onReason={setReason}
              />
            )}
          </div>
        )}
      </DialogShell.Body>
      <DialogShell.Footer>
        {/* Risky action: the safe choice comes first and takes the initial focus (13.12.3). */}
        <Button variant="outline" autoFocus onClick={onClose}>
          {t('cancel')}
        </Button>
        {step > 1 ? (
          <Button variant="ghost" onClick={() => setStep((step - 1) as Step)}>
            {t('back')}
          </Button>
        ) : null}
        {step < 3 ? (
          <Button disabled={!canProceed} onClick={() => setStep((step + 1) as Step)}>
            {t('next')}
          </Button>
        ) : (
          <Button variant="destructive" disabled={!canSubmit} onClick={submit}>
            {t('submit')}
          </Button>
        )}
      </DialogShell.Footer>
      {transferring ? (
        <TransferOwnershipDialog
          open
          onOpenChange={(open) => (open ? undefined : setTransferring(null))}
          resourceType={transferring.resource_type}
          resourceId={transferring.resource_id}
          resourceName={transferring.name}
          ownerId={member.userId}
          isOwner={false}
          onTransferred={() => void preview.refetch()}
        />
      ) : null}
    </>
  )
}

function StepIndicator({ step }: { step: Step }) {
  const t = useTranslations('orgAdmin.memberRemoval.steps')
  return (
    <ol className="flex flex-wrap gap-3 text-sm" aria-label={t('label')}>
      {STEPS.map((value) => (
        <li
          key={value}
          aria-current={value === step ? 'step' : undefined}
          className={cn(value === step ? 'font-medium text-foreground' : 'text-muted-foreground')}
        >
          {t('item', { number: value, label: t(`s${value}`) })}
        </li>
      ))}
    </ol>
  )
}
