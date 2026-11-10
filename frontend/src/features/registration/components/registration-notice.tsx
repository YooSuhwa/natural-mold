'use client'

import { useTranslations } from 'next-intl'
import { toast } from 'sonner'

import { useApiErrorMessage } from '@/components/shared/api-error-state'
import { Button } from '@/components/ui/button'
import { useRegistrationStatus, useRequestRegistration } from '@/lib/hooks/use-registration'
import type { RegistrationResourceType, RegistrationState } from '@/lib/types/approval'

/**
 * What the registrant of an unapproved tool or MCP server is told (plan 3.7): it cannot be
 * attached to agents yet, what the administrator answered, and a way to ask again.
 */
export function RegistrationNotice({
  type,
  id,
  state,
  canEdit,
}: {
  type: RegistrationResourceType
  id: string
  state: RegistrationState | undefined
  /** Editors read the last answer and may ask again; others only see the state. */
  canEdit: boolean
}) {
  const t = useTranslations('registration.notice')
  const waiting = !!state && state !== 'approved'
  const status = useRegistrationStatus(type, id, waiting && canEdit)
  const request = useRequestRegistration(type, id)
  const errorMessage = useApiErrorMessage()
  if (!waiting) return null
  const last = status.data?.request ?? null
  // The administrator's note is plain text from another person: shown as characters only.
  const note = last?.status === 'rejected' ? last.reason : null

  return (
    <div
      role="status"
      data-registration-notice={state}
      className="space-y-2 rounded-md border border-border/60 bg-muted/40 p-3 text-xs"
    >
      <p className="font-medium text-foreground">{t(`${state}.title`)}</p>
      <p className="text-muted-foreground">{t(`${state}.body`)}</p>
      {note ? (
        <p className="whitespace-pre-wrap break-words text-foreground">{t('note', { note })}</p>
      ) : null}
      {status.data?.can_request ? (
        <Button
          size="sm"
          variant="outline"
          disabled={request.isPending}
          onClick={() =>
            request.mutate(undefined, {
              onSuccess: () => toast.success(t('requested')),
              onError: (error) => toast.error(errorMessage(error, t('requestFailed'))),
            })
          }
        >
          {t('request')}
        </Button>
      ) : null}
    </div>
  )
}
