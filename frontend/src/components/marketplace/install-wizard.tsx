'use client'

import Link from 'next/link'
import { useMemo, useState } from 'react'
import { toast } from 'sonner'

import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select'
import { DialogShell } from '@/components/shared/dialog-shell'
import { CredentialBadge } from '@/components/marketplace/badges/credential-badge'
import { OriginBadge } from '@/components/marketplace/badges/origin-badge'
import { SupportBadge } from '@/components/marketplace/badges/support-badge'
import { ApiError } from '@/lib/api/client'
import { useInstallItem, useMarketplaceVersion } from '@/lib/hooks/use-marketplace'
import { useCredentials } from '@/lib/hooks/use-credentials'
import type { Credential } from '@/lib/types/credential'
import type {
  CredentialRequirement,
  MarketplaceItem,
} from '@/lib/types/marketplace'
import { cn } from '@/lib/utils'

type Step = 'review' | 'credentials' | 'confirm' | 'done'

const SKIP_VALUE = '__skip__'

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

function parseRequirements(
  raw: Array<Record<string, unknown>> | null | undefined,
): CredentialRequirement[] {
  if (!Array.isArray(raw)) return []
  return raw
    .filter((r): r is Record<string, unknown> => {
      if (!r || typeof r !== 'object') return false
      return typeof r.key === 'string' && typeof r.definition_key === 'string'
    })
    .map((r) => ({
      key: r.key as string,
      definition_key: r.definition_key as string,
      required: (r.required as boolean) ?? true,
      label: (r.label as string) ?? (r.key as string),
      description: (r.description as string | null) ?? null,
      fields: Array.isArray(r.fields) ? (r.fields as string[]) : [],
      injection: (r.injection as CredentialRequirement['injection']) ?? 'env',
      scope: (r.scope as CredentialRequirement['scope']) ?? 'user',
    }))
}

function InstallWizardInner({ item, open, onOpenChange }: InstallWizardProps) {
  const versionId = item?.latest_version?.id ?? null
  const { data: versionDetail } = useMarketplaceVersion(versionId)
  const requirements = useMemo(
    () => parseRequirements(versionDetail?.credential_requirements),
    [versionDetail],
  )
  const hasAnyRequirement = requirements.length > 0
  const steps: Step[] = hasAnyRequirement
    ? ['review', 'credentials', 'confirm', 'done']
    : ['review', 'confirm', 'done']

  // Derive initial step from props: needs_setup → credentials (or confirm).
  const initialStep: Step =
    item?.installation.status === 'needs_setup'
      ? hasAnyRequirement
        ? 'credentials'
        : 'confirm'
      : 'review'

  const [step, setStep] = useState<Step>(initialStep)
  const [nameOverride, setNameOverride] = useState<string>('')
  const [bindings, setBindings] = useState<Record<string, string>>({})
  const [errorMessage, setErrorMessage] = useState<string | null>(null)
  const install = useInstallItem(item?.id ?? '')

  if (!item) return null

  const missingRequiredKeys = requirements
    .filter((r) => r.required && !bindings[r.key])
    .map((r) => r.key)

  async function handleInstall() {
    if (!item) return
    setErrorMessage(null)
    try {
      await install.mutateAsync({
        name_override: nameOverride || undefined,
        credential_bindings: bindings,
        // Missing required bindings → server marks install as needs_setup.
        // Optional credentials can be left empty without forcing setup.
        install_missing_credentials: 'needs_setup',
        install_mode: 'reuse_or_update',
      })
      setStep('done')
      const willNeedSetup = missingRequiredKeys.length > 0
      toast.success(
        willNeedSetup
          ? `Installed ${item.name} (needs setup)`
          : `Installed ${item.name}`,
      )
    } catch (err) {
      if (err instanceof ApiError) {
        setErrorMessage(
          err.code === 'marketplace_item_not_found'
            ? '이 항목을 찾을 수 없거나 접근 권한이 없습니다.'
            : err.code === 'marketplace_item_disabled'
              ? '이 항목은 비활성화되었습니다.'
              : err.code === 'marketplace_credential_mismatch'
                ? '선택한 자격증명이 요구사항과 일치하지 않습니다. 다시 선택해주세요.'
                : err.code === 'marketplace_credential_required'
                  ? '필수 자격증명이 누락되었습니다.'
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
            <CredentialsStep
              requirements={requirements}
              bindings={bindings}
              setBindings={setBindings}
            />
          ) : null}

          {step === 'confirm' ? (
            <ConfirmStep
              item={item}
              nameOverride={nameOverride}
              requirements={requirements}
              bindings={bindings}
              missingRequiredKeys={missingRequiredKeys}
            />
          ) : null}

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

function CredentialsStep({
  requirements,
  bindings,
  setBindings,
}: {
  requirements: CredentialRequirement[]
  bindings: Record<string, string>
  setBindings: (b: Record<string, string>) => void
}) {
  const { data: credentials, isLoading } = useCredentials()

  function update(key: string, value: string) {
    if (value === SKIP_VALUE) {
      const next = { ...bindings }
      delete next[key]
      setBindings(next)
    } else {
      setBindings({ ...bindings, [key]: value })
    }
  }

  return (
    <div className="space-y-4 text-sm">
      <p className="text-muted-foreground">
        Skill 실행에 필요한 자격증명을 연결합니다. 필수가 아닌 항목은 건너뛸 수 있고, 필수 항목을
        나중에 연결하면 설치는 <code className="font-mono text-xs">needs_setup</code> 상태가
        됩니다.
      </p>

      <ul className="space-y-3">
        {requirements.map((req) => (
          <RequirementRow
            key={req.key}
            requirement={req}
            value={bindings[req.key] ?? SKIP_VALUE}
            onChange={(v) => update(req.key, v)}
            credentials={credentials ?? []}
            isLoading={isLoading}
          />
        ))}
      </ul>
    </div>
  )
}

function RequirementRow({
  requirement,
  value,
  onChange,
  credentials,
  isLoading,
}: {
  requirement: CredentialRequirement
  value: string
  onChange: (v: string) => void
  credentials: Credential[]
  isLoading: boolean
}) {
  const matched = credentials.filter(
    (c) => c.definition_key === requirement.definition_key,
  )
  const hasMatch = matched.length > 0
  const isRequiredAndMissing = requirement.required && value === SKIP_VALUE

  return (
    <li className="rounded-md border border-border bg-background p-3">
      <div className="mb-2 flex items-start justify-between gap-2">
        <div>
          <p className="font-medium text-foreground">
            {requirement.label}
            {requirement.required ? (
              <span className="ml-1.5 text-destructive">*</span>
            ) : (
              <span className="ml-1.5 text-xs font-normal text-muted-foreground">(optional)</span>
            )}
          </p>
          {requirement.description ? (
            <p className="mt-0.5 text-xs text-muted-foreground">{requirement.description}</p>
          ) : null}
          <p className="mt-0.5 text-xs text-muted-foreground">
            Type: <code className="font-mono">{requirement.definition_key}</code>
          </p>
        </div>
      </div>

      {isLoading ? (
        <p className="text-xs text-muted-foreground">Loading credentials…</p>
      ) : hasMatch ? (
        <Select value={value} onValueChange={(v) => onChange(v ?? SKIP_VALUE)}>
          <SelectTrigger>
            <SelectValue placeholder="자격증명 선택…" />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value={SKIP_VALUE}>나중에 설정 (needs setup)</SelectItem>
            {matched.map((c) => (
              <SelectItem key={c.id} value={c.id}>
                {c.name}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
      ) : (
        <div className="rounded-md border border-dashed border-status-warn/40 bg-status-warn/5 p-2 text-xs">
          <p className="text-status-warn">
            <code className="font-mono">{requirement.definition_key}</code> 타입 자격증명이
            없습니다.
          </p>
          <Link
            href="/credentials"
            className="mt-1 inline-block text-primary-strong hover:underline"
          >
            Credentials 페이지에서 만들기 →
          </Link>
        </div>
      )}

      {isRequiredAndMissing ? (
        <p className="mt-2 text-xs text-status-warn">
          연결하지 않으면 설치 후 <code className="font-mono">needs_setup</code> 상태로 표시됩니다.
        </p>
      ) : null}
    </li>
  )
}

function ConfirmStep({
  item,
  nameOverride,
  requirements,
  bindings,
  missingRequiredKeys,
}: {
  item: MarketplaceItem
  nameOverride: string
  requirements: CredentialRequirement[]
  bindings: Record<string, string>
  missingRequiredKeys: string[]
}) {
  const boundCount = Object.keys(bindings).length
  const willNeedSetup = missingRequiredKeys.length > 0

  return (
    <div className="space-y-3 text-sm">
      <p>The following will happen:</p>
      <ul className="list-inside list-disc space-y-1 text-muted-foreground">
        <li>Create a skill row owned by you (origin: imported_by_me)</li>
        <li>Copy package files into your data directory</li>
        {boundCount > 0 ? (
          <li>
            Bind <span className="font-medium text-foreground">{boundCount}</span> credential(s):{' '}
            {requirements
              .filter((r) => bindings[r.key])
              .map((r) => r.label)
              .join(', ')}
          </li>
        ) : null}
        {willNeedSetup ? (
          <li className="text-status-warn">
            Mark as <code className="font-mono text-xs">needs_setup</code> — required credential(s)
            missing: {missingRequiredKeys.join(', ')}
          </li>
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
