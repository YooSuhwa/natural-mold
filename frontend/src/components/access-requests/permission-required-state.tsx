'use client'

import { useState, type ReactNode } from 'react'
import { useTranslations } from 'next-intl'
import { LockKeyholeIcon } from 'lucide-react'

import { Button } from '@/components/ui/button'
import { ApiError } from '@/lib/api/client'
import type { RequestLevel, RequestableType } from '@/lib/types/access-request'

import { RequestAccessDialog } from './request-access-dialog'

/** 8.4: a visible resource the caller lacks the permission for (403 `permission_required`). */
export function isPermissionRequired(error: unknown): error is ApiError {
  return error instanceof ApiError && error.code === 'permission_required'
}

interface PermissionRequiredStateProps {
  error: ApiError
  resourceType: RequestableType
  resourceId: string
  resourceName?: string | null
  levels?: readonly RequestLevel[]
  /** Extra actions (e.g. back to the list). */
  children?: ReactNode
}

/** S02/S18 "권한 안내" with the request button; hidden when the server says no request. */
export function PermissionRequiredState({
  error,
  resourceType,
  resourceId,
  resourceName,
  levels,
  children,
}: PermissionRequiredStateProps) {
  const t = useTranslations('sharing.request')
  const [open, setOpen] = useState(false)
  const requestable = error.details?.request_access !== false

  return (
    <div role="alert" className="moldy-panel max-w-md space-y-3 p-6 text-center">
      <LockKeyholeIcon className="mx-auto size-8 text-muted-foreground" />
      <h2 className="text-base font-semibold">{t('stateTitle')}</h2>
      <p className="text-sm text-muted-foreground">
        {requestable ? t('stateDescription') : t('stateNoRequest')}
      </p>
      <div className="flex flex-wrap justify-center gap-2">
        {requestable ? <Button onClick={() => setOpen(true)}>{t('open')}</Button> : null}
        {children}
      </div>
      <RequestAccessDialog
        open={open}
        onOpenChange={setOpen}
        resourceType={resourceType}
        resourceId={resourceId}
        resourceName={resourceName}
        levels={levels}
      />
    </div>
  )
}
