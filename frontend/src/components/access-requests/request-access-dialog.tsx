'use client'

import { useId, useState, type FormEvent } from 'react'
import { useTranslations } from 'next-intl'
import { KeyRoundIcon } from 'lucide-react'
import { toast } from 'sonner'

import { DialogShell } from '@/components/shared/dialog-shell'
import { Button } from '@/components/ui/button'
import { Textarea } from '@/components/ui/textarea'
import { ApiError } from '@/lib/api/client'
import { useCreateAccessRequest } from '@/lib/hooks/use-access-requests'
import type { RequestLevel, RequestableType } from '@/lib/types/access-request'

/** Levels a member may ask for: up to "edit"; "manage" is only given by the owner (S18). */
export const REQUEST_LEVELS: readonly RequestLevel[] = ['viewer', 'editor']

interface RequestAccessDialogProps {
  open: boolean
  onOpenChange: (open: boolean) => void
  resourceType: RequestableType
  resourceId: string
  /** Omitted when the caller may not see the name ("이 에이전트의 관리자에게 요청"). */
  resourceName?: string | null
  levels?: readonly RequestLevel[]
}

function errorKey(error: unknown): 'alreadyGranted' | 'rateLimited' | 'failed' {
  if (!(error instanceof ApiError)) return 'failed'
  if (error.code === 'quota_exceeded') return 'rateLimited'
  if (error.code === 'grant_invalid' && error.details?.reason === 'already_granted') {
    return 'alreadyGranted'
  }
  return 'failed'
}

/** S18 "권한 요청": level + reason, sent to the resource's managers (plan 10.9). */
export function RequestAccessDialog({
  open,
  onOpenChange,
  resourceType,
  resourceId,
  resourceName,
  levels = REQUEST_LEVELS,
}: RequestAccessDialogProps) {
  const t = useTranslations('sharing')
  const id = useId()
  const create = useCreateAccessRequest()
  const [level, setLevel] = useState<RequestLevel>(levels[levels.length - 1] ?? 'editor')
  const [reason, setReason] = useState('')

  const submit = (event: FormEvent) => {
    event.preventDefault()
    const body = {
      resource_type: resourceType,
      resource_id: resourceId,
      level,
      reason: reason.trim() || null,
    }
    create.mutate(body, {
      // 202 (nothing revealed) and 201/200 read the same to the requester (plan 8.4).
      onSuccess: () => {
        toast.success(t('request.sent'))
        onOpenChange(false)
      },
      onError: (error) => toast.error(t(`request.${errorKey(error)}`)),
    })
  }

  return (
    <DialogShell open={open} onOpenChange={onOpenChange} size="sm" height="auto">
      <DialogShell.Header
        icon={<KeyRoundIcon className="size-5" />}
        title={resourceName ? t('request.titleNamed', { name: resourceName }) : t('request.title')}
        description={t('request.description')}
      />
      <DialogShell.Body>
        <form className="space-y-4" onSubmit={submit}>
          <fieldset className="space-y-2">
            <legend className="text-xs font-medium text-muted-foreground">
              {t('request.level')}
            </legend>
            {levels.map((value) => (
              <div
                key={value}
                className="flex min-h-10 items-start gap-2 rounded-md border border-border px-3 py-2 has-checked:border-primary-strong"
              >
                <input
                  id={`${id}-${value}`}
                  type="radio"
                  name={`${id}-level`}
                  value={value}
                  checked={level === value}
                  onChange={() => setLevel(value)}
                  aria-label={t(`level.${value}`)}
                  aria-describedby={`${id}-${value}-help`}
                  className="mt-1"
                />
                <div className="min-w-0">
                  <label
                    htmlFor={`${id}-${value}`}
                    className="block cursor-pointer text-sm text-foreground"
                  >
                    {t(`level.${value}`)}
                  </label>
                  <span id={`${id}-${value}-help`} className="block text-xs text-muted-foreground">
                    {t(`levelHelp.${value}`)}
                  </span>
                </div>
              </div>
            ))}
          </fieldset>
          <div className="space-y-1">
            <label htmlFor={`${id}-reason`} className="text-xs font-medium text-muted-foreground">
              {t('request.reason')}
            </label>
            <Textarea
              id={`${id}-reason`}
              value={reason}
              maxLength={500}
              placeholder={t('request.reasonPlaceholder')}
              onChange={(event) => setReason(event.target.value)}
            />
          </div>
          <div className="flex justify-end gap-2">
            <Button type="button" variant="outline" onClick={() => onOpenChange(false)}>
              {t('request.cancel')}
            </Button>
            <Button type="submit" disabled={create.isPending}>
              {create.isPending ? t('request.sending') : t('request.submit')}
            </Button>
          </div>
        </form>
      </DialogShell.Body>
    </DialogShell>
  )
}
