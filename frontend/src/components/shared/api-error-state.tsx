'use client'

import { useState, type ReactNode } from 'react'
import Link from 'next/link'
import { useTranslations } from 'next-intl'
import { AlertCircleIcon, LockKeyholeIcon, SearchXIcon } from 'lucide-react'

import { PermissionRequiredState } from '@/components/access-requests/permission-required-state'
import { ReadinessDialog } from '@/components/readiness/readiness-dialog'
import { Button, buttonVariants } from '@/components/ui/button'
import { mapApiErrorToUiState, type ApiErrorUiKind } from '@/lib/api/client'
import type { RequestLevel, RequestableType } from '@/lib/types/access-request'
import { cn } from '@/lib/utils'

/** i18n key under `common.apiError` per kind. */
export const API_ERROR_MESSAGE_KEY: Record<ApiErrorUiKind, string> = {
  unauthenticated: 'unauthenticated',
  permission_required: 'permissionRequired',
  capability_required: 'capabilityRequired',
  org_access_denied: 'orgAccessDenied',
  tenant_suspended: 'tenantSuspended',
  not_found: 'notFound',
  agent_not_ready: 'agentNotReady',
  quota_exceeded: 'quotaExceeded',
  authz_unavailable: 'authzUnavailable',
  unknown: 'unknown',
}

export interface ApiErrorResource {
  type: RequestableType
  id: string
  name?: string | null
  levels?: readonly RequestLevel[]
}

interface ApiErrorStateProps {
  error: unknown
  /** The resource the screen shows: a 403 `permission_required` offers an access request. */
  resource?: ApiErrorResource
  onRetry?: () => void
  /** Extra actions (e.g. back to the list). */
  children?: ReactNode
}

/**
 * The shared screen-level error (plan 13.1): 401, 403 `permission_required` (H-01 request
 * access), `capability_required`, `org_access_denied`, 404, 409 `agent_not_ready`
 * (S21 panel), 429 `quota_exceeded`, 503 `authz_unavailable`, anything else.
 */
export function ApiErrorState({ error, resource, onRetry, children }: ApiErrorStateProps) {
  const t = useTranslations('common.apiError')
  const ui = mapApiErrorToUiState(error)
  const [readinessOpen, setReadinessOpen] = useState(false)

  if (ui.kind === 'permission_required' && ui.error && resource) {
    return (
      <PermissionRequiredState
        error={ui.error}
        resourceType={resource.type}
        resourceId={resource.id}
        resourceName={resource.name}
        levels={resource.levels}
      >
        {children}
      </PermissionRequiredState>
    )
  }

  const key = API_ERROR_MESSAGE_KEY[ui.kind]
  const Icon =
    ui.kind === 'not_found'
      ? SearchXIcon
      : ui.kind.endsWith('_required') || ui.kind === 'org_access_denied'
        ? LockKeyholeIcon
        : AlertCircleIcon
  const readinessAgent = ui.kind === 'agent_not_ready' ? ui.agentId : null

  return (
    <div role="alert" data-error-kind={ui.kind} className="moldy-error-state">
      <Icon className="size-8 text-muted-foreground" aria-hidden="true" />
      <div>
        <p className="text-sm font-semibold text-foreground">{t(`${key}.title`)}</p>
        <p className="mt-1 text-sm text-muted-foreground">{t(`${key}.description`)}</p>
      </div>
      <div className="flex flex-wrap justify-center gap-2">
        {ui.kind === 'unauthenticated' ? (
          <Link href="/login" className={cn(buttonVariants({ size: 'sm' }))}>
            {t('login')}
          </Link>
        ) : null}
        {readinessAgent ? (
          <Button size="sm" onClick={() => setReadinessOpen(true)}>
            {t('agentNotReady.check')}
          </Button>
        ) : null}
        {ui.retryable && onRetry ? (
          <Button variant="outline" size="sm" onClick={onRetry}>
            {t('retry')}
          </Button>
        ) : null}
        {children}
      </div>
      {readinessAgent ? (
        <ReadinessDialog
          open={readinessOpen}
          onOpenChange={setReadinessOpen}
          agentId={readinessAgent}
        />
      ) : null}
    </div>
  )
}

/** Toast text for a failed mutation: the shared message, or `fallback` for unknown errors. */
export function useApiErrorMessage(): (error: unknown, fallback: string) => string {
  const t = useTranslations('common.apiError')
  return (error, fallback) => {
    const { kind } = mapApiErrorToUiState(error)
    return kind === 'unknown' ? fallback : t(`${API_ERROR_MESSAGE_KEY[kind]}.description`)
  }
}
