'use client'

import { useState } from 'react'
import { toast } from 'sonner'

import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { DialogShell } from '@/components/shared/dialog-shell'
import { CredentialBadge } from '@/components/marketplace/badges/credential-badge'
import { OriginBadge } from '@/components/marketplace/badges/origin-badge'
import { SupportBadge } from '@/components/marketplace/badges/support-badge'
import { ApiError } from '@/lib/api/client'
import { useInstallItem } from '@/lib/hooks/use-marketplace'
import type { MarketplaceItem } from '@/lib/types/marketplace'
import { cn } from '@/lib/utils'

type Step = 'review' | 'credentials' | 'confirm' | 'done'

interface InstallWizardProps {
  item: MarketplaceItem | null
  open: boolean
  onOpenChange: (open: boolean) => void
}

export function InstallWizard({ item, open, onOpenChange }: InstallWizardProps) {
  // remount on item change so internal state resets
  return (
    <InstallWizardInner
      key={item?.id ?? 'closed'}
      item={item}
      open={open}
      onOpenChange={onOpenChange}
    />
  )
}

function InstallWizardInner({ item, open, onOpenChange }: InstallWizardProps) {
  const hasRequired = !!item && item.credential_summary.required_count > 0
  const steps: Step[] = hasRequired
    ? ['review', 'credentials', 'confirm', 'done']
    : ['review', 'confirm', 'done']

  // Derive initial step from props: needs_setup → credentials (or confirm).
  const initialStep: Step =
    item?.installation.status === 'needs_setup'
      ? hasRequired
        ? 'credentials'
        : 'confirm'
      : 'review'

  const [step, setStep] = useState<Step>(initialStep)
  const [nameOverride, setNameOverride] = useState<string>('')
  const [errorMessage, setErrorMessage] = useState<string | null>(null)
  const install = useInstallItem(item?.id ?? '')

  if (!item) return null

  async function handleInstall() {
    if (!item) return
    setErrorMessage(null)
    try {
      await install.mutateAsync({
        name_override: nameOverride || undefined,
        install_missing_credentials: hasRequired ? 'needs_setup' : 'needs_setup',
        install_mode: 'reuse_or_update',
      })
      setStep('done')
      toast.success(`Installed ${item.name}`)
    } catch (err) {
      if (err instanceof ApiError) {
        setErrorMessage(
          err.code === 'marketplace_item_not_found'
            ? '이 항목을 찾을 수 없거나 접근 권한이 없습니다.'
            : err.code === 'marketplace_item_disabled'
              ? '이 항목은 비활성화되었습니다.'
              : err.message || '설치에 실패했습니다.',
        )
      } else {
        setErrorMessage('네트워크 오류가 발생했습니다. 다시 시도하세요.')
      }
    }
  }

  const stepIndex = steps.indexOf(step)
  const isFirst = stepIndex === 0
  const isLast = step === 'done'

  function goNext() {
    if (stepIndex < steps.length - 1) {
      const next = steps[stepIndex + 1]
      if (next === 'done') {
        void handleInstall()
      } else {
        setStep(next)
      }
    }
  }

  function goBack() {
    if (stepIndex > 0) setStep(steps[stepIndex - 1])
  }

  return (
    <DialogShell open={open} onOpenChange={onOpenChange} size="lg" height="fixed">
      <DialogShell.Header
        title={`Install ${item.name}`}
        description={item.description ?? undefined}
        actions={
          <>
            <OriginBadge summary={item.origin_summary} />
            <CredentialBadge summary={item.credential_summary} />
            <SupportBadge profile={item.execution_profile} />
          </>
        }
      />

      <DialogShell.Split>
        <DialogShell.Sidebar>
          <ol className="space-y-1 text-sm" role="list">
            {steps.map((s, i) => (
              <li
                key={s}
                aria-current={s === step ? 'step' : undefined}
                className={cn(
                  'flex items-center gap-2 rounded-md px-2 py-1.5',
                  s === step
                    ? 'bg-primary/15 font-medium text-primary-strong'
                    : 'text-muted-foreground',
                )}
              >
                <span className="inline-flex size-5 items-center justify-center rounded-full bg-muted text-[10px]">
                  {i + 1}
                </span>
                <span className="capitalize">{labelForStep(s)}</span>
              </li>
            ))}
          </ol>
        </DialogShell.Sidebar>

        <DialogShell.Body>
          {errorMessage ? (
            <div className="rounded-md border border-destructive/40 bg-destructive/10 p-3 text-sm text-destructive">
              {errorMessage}
            </div>
          ) : null}

          {step === 'review' ? (
            <ReviewStep item={item} nameOverride={nameOverride} setName={setNameOverride} />
          ) : null}

          {step === 'credentials' ? (
            <CredentialsStep item={item} />
          ) : null}

          {step === 'confirm' ? <ConfirmStep item={item} nameOverride={nameOverride} /> : null}

          {step === 'done' ? <DoneStep item={item} onClose={() => onOpenChange(false)} /> : null}
        </DialogShell.Body>
      </DialogShell.Split>

      <DialogShell.Footer>
        {isLast ? (
          <Button onClick={() => onOpenChange(false)}>Close</Button>
        ) : (
          <>
            <Button variant="outline" onClick={() => onOpenChange(false)}>
              Cancel
            </Button>
            {!isFirst ? (
              <Button variant="outline" onClick={goBack}>
                Back
              </Button>
            ) : null}
            <Button onClick={goNext} disabled={install.isPending}>
              {stepIndex === steps.length - 2
                ? install.isPending
                  ? 'Installing…'
                  : 'Install'
                : 'Next'}
            </Button>
          </>
        )}
      </DialogShell.Footer>
    </DialogShell>
  )
}

function labelForStep(step: Step): string {
  if (step === 'review') return 'Review'
  if (step === 'credentials') return 'Credentials'
  if (step === 'confirm') return 'Confirm'
  return 'Done'
}

function ReviewStep({
  item,
  nameOverride,
  setName,
}: {
  item: MarketplaceItem
  nameOverride: string
  setName: (v: string) => void
}) {
  return (
    <div className="space-y-4">
      <div className="text-sm text-muted-foreground">
        <p>
          Resource type: <span className="font-medium text-foreground">{item.resource_type}</span>
        </p>
        {item.latest_version ? (
          <p>
            Latest version:{' '}
            <span className="font-medium text-foreground">v{item.latest_version.version_label}</span>
          </p>
        ) : null}
        {item.execution_profile?.support_level ? (
          <p>
            Support level:{' '}
            <span className="font-medium text-foreground">
              {item.execution_profile.support_level}
            </span>
          </p>
        ) : null}
      </div>

      <div className="space-y-1.5">
        <label htmlFor="name-override" className="block">
          Custom name (optional)
        </label>
        <Input
          id="name-override"
          value={nameOverride}
          onChange={(e) => setName(e.target.value)}
          placeholder={item.name}
        />
      </div>
    </div>
  )
}

function CredentialsStep({ item }: { item: MarketplaceItem }) {
  return (
    <div className="space-y-3 text-sm">
      <p className="text-muted-foreground">
        This skill requires {item.credential_summary.required_count} credential(s). You can connect
        them now or skip and configure later (the installation will be marked{' '}
        <span className="font-medium">needs_setup</span>).
      </p>
      <div className="rounded-md border border-status-warn/30 bg-status-warn/10 p-3 text-status-warn">
        Credential binding wizard is part of the next slice. For now we install with{' '}
        <code className="font-mono text-xs">needs_setup</code> mode so you can wire credentials on
        the skill detail page.
      </div>
    </div>
  )
}

function ConfirmStep({ item, nameOverride }: { item: MarketplaceItem; nameOverride: string }) {
  return (
    <div className="space-y-3 text-sm">
      <p>The following will happen:</p>
      <ul className="list-inside list-disc space-y-1 text-muted-foreground">
        <li>Create a skill row owned by you (origin: imported_by_me)</li>
        <li>Copy package files into your data directory</li>
        {item.credential_summary.required_count > 0 ? (
          <li>Mark as <code className="font-mono text-xs">needs_setup</code> pending credential binding</li>
        ) : null}
      </ul>
      <div className="rounded-md bg-muted p-3">
        <p>
          <span className="text-muted-foreground">Name:</span>{' '}
          <span className="font-medium">{nameOverride || item.name}</span>
        </p>
        {item.latest_version ? (
          <p>
            <span className="text-muted-foreground">Version:</span>{' '}
            <span className="font-medium">v{item.latest_version.version_label}</span>
          </p>
        ) : null}
      </div>
    </div>
  )
}

function DoneStep({ item, onClose }: { item: MarketplaceItem; onClose: () => void }) {
  return (
    <div className="flex flex-col items-center justify-center space-y-3 text-center">
      <p className="text-base font-medium">Installed {item.name}</p>
      <p className="text-sm text-muted-foreground">
        Open the skill or attach it to an agent to start using it.
      </p>
      <Button variant="outline" onClick={onClose}>
        Close
      </Button>
    </div>
  )
}
