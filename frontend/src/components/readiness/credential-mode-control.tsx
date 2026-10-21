'use client'

import { useState } from 'react'
import { useTranslations } from 'next-intl'
import { toast } from 'sonner'

import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select'
import { ApiError } from '@/lib/api/client'
import { useSetCredentialMode } from '@/lib/hooks/use-readiness'
import type { BindableResourceType, CredentialMode } from '@/lib/types/readiness'

type Choice = 'resource' | 'per_user'

export interface CredentialModeControlProps {
  readonly resourceType: BindableResourceType
  readonly resourceId: string
  readonly mode: CredentialMode
  readonly credentialId: string | null
  readonly definitionKey: string | null
  /** Types a per-user connection may require (tool definition keys, or every type for MCP). */
  readonly acceptedKeys: readonly string[]
  readonly disabled?: boolean
}

function errorKey(error: unknown): 'reshareBlocked' | 'personalAccount' | 'invalid' | 'failed' {
  if (!(error instanceof ApiError)) return 'failed'
  if (error.code === 'credential_reshare_blocked') return 'reshareBlocked'
  if (error.code === 'personal_account_shared') return 'personalAccount'
  if (error.code === 'credential_mode_invalid') return 'invalid'
  return 'failed'
}

/**
 * F-04 (plan D15, 11.3): whether everyone who may use the resource runs on its own account
 * (`shared`, or `none` without one) or each caller connects their own (`per_user`).
 */
export function CredentialModeControl({
  resourceType,
  resourceId,
  mode,
  credentialId,
  definitionKey,
  acceptedKeys,
  disabled,
}: CredentialModeControlProps) {
  const t = useTranslations('readiness.mode')
  const setMode = useSetCredentialMode()
  const choice: Choice = mode === 'per_user' ? 'per_user' : 'resource'
  // A per-user resource records the one type its callers connect; ask only when it is open.
  const needsType = !credentialId && !definitionKey && acceptedKeys.length > 1
  const [pendingType, setPendingType] = useState<string | null>(null)

  async function apply(next: Choice, type?: string | null) {
    const target: CredentialMode =
      next === 'per_user' ? 'per_user' : credentialId ? 'shared' : 'none'
    const key =
      next === 'per_user'
        ? (type ?? definitionKey ?? (acceptedKeys.length === 1 ? acceptedKeys[0] : null))
        : null
    try {
      await setMode.mutateAsync({ resourceType, resourceId, mode: target, definitionKey: key })
      setPendingType(null)
      toast.success(t(next === 'per_user' ? 'toast.perUser' : 'toast.resource'))
    } catch (error) {
      toast.error(t(`errors.${errorKey(error)}`))
    }
  }

  function onChoice(value: string | null) {
    if (value !== 'resource' && value !== 'per_user') return
    if (value === choice) return
    if (value === 'per_user' && needsType) {
      setPendingType('')
      return
    }
    void apply(value)
  }

  return (
    <div className="space-y-1.5">
      <label className="text-xs font-medium" id={`credential-mode-${resourceId}`}>
        {t('label')}
      </label>
      <Select value={choice} onValueChange={onChoice} disabled={disabled || setMode.isPending}>
        <SelectTrigger
          className="w-full"
          aria-labelledby={`credential-mode-${resourceId}`}
          data-testid="credential-mode-select"
        >
          <SelectValue>
            {(selected: unknown) => t(selected === 'per_user' ? 'perUser' : 'resource')}
          </SelectValue>
        </SelectTrigger>
        <SelectContent>
          <SelectItem value="resource">{t('resource')}</SelectItem>
          <SelectItem value="per_user">{t('perUser')}</SelectItem>
        </SelectContent>
      </Select>
      <p className="text-xs text-muted-foreground">
        {t(choice === 'per_user' ? 'perUserHelp' : 'resourceHelp')}
      </p>
      {pendingType !== null ? (
        <Select
          value={pendingType || null}
          onValueChange={(value) => value && void apply('per_user', value)}
          disabled={setMode.isPending}
        >
          <SelectTrigger className="w-full" data-testid="credential-mode-type-select">
            <SelectValue placeholder={t('chooseType')} />
          </SelectTrigger>
          <SelectContent>
            {acceptedKeys.map((key) => (
              <SelectItem key={key} value={key}>
                {key}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
      ) : null}
    </div>
  )
}
