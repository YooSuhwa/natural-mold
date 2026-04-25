'use client'

import { useState, type ReactNode } from 'react'
import { useTranslations } from 'next-intl'
import { CheckCircleIcon, LinkIcon, Loader2Icon } from 'lucide-react'

import {
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog'
import { Button } from '@/components/ui/button'
import { Skeleton } from '@/components/ui/skeleton'
import { CredentialFormDialog } from '@/components/tool/credential-form-dialog'
import { CredentialSelect, CREDENTIAL_NONE } from '@/components/tool/credential-select'
import type { Credential } from '@/lib/types'

interface BindingDialogShellProps {
  /** Header icon (KeyIcon / ServerIcon 등). */
  icon: ReactNode
  title: ReactNode
  description?: ReactNode
  /** 공유 credential 경고 등 body 고유 상단 alert. */
  topSlot?: ReactNode
  mode: string
  onModeChange: (v: string) => void
  credentials: Credential[]
  /** 연결 목록 비동기 로딩 중이면 true → CredentialSelect 자리에 Skeleton. */
  connectionsLoading?: boolean
  createFormDefaultProvider?: string
  onCredentialCreated?: (c: Credential) => void
  onSave: () => void | Promise<void>
  onCancel: () => void
  isPending: boolean
  saveDisabled?: boolean
}

/**
 * Prebuilt/Custom/Mcp body가 공유하는 UI chrome (Header/Footer/Credential section +
 * 내부에 CredentialFormDialog 소유). hydration/save 로직은 각 body가 소유한다.
 */
export function BindingDialogShell({
  icon,
  title,
  description,
  topSlot,
  mode,
  onModeChange,
  credentials,
  connectionsLoading = false,
  createFormDefaultProvider,
  onCredentialCreated,
  onSave,
  onCancel,
  isPending,
  saveDisabled,
}: BindingDialogShellProps) {
  const t = useTranslations('connections.bindingDialog')
  const tc = useTranslations('common')
  const tCred = useTranslations('connections.credentialSelect')
  const [createOpen, setCreateOpen] = useState(false)
  const disabled = saveDisabled ?? isPending

  return (
    <>
      <DialogHeader>
        <DialogTitle className="flex items-center gap-2">
          {icon}
          {title}
        </DialogTitle>
        {description && <DialogDescription>{description}</DialogDescription>}
      </DialogHeader>

      <div className="space-y-4 py-2">
        {topSlot}

        <div className="space-y-2">
          <label className="text-sm font-medium flex items-center gap-1.5">
            <LinkIcon className="size-3.5" />
            {tCred('label')}
          </label>
          {connectionsLoading ? (
            <Skeleton className="h-9 w-full" />
          ) : (
            <CredentialSelect
              value={mode}
              onValueChange={onModeChange}
              onCreateRequested={() => setCreateOpen(true)}
              credentials={credentials}
            />
          )}
        </div>

        {mode !== CREDENTIAL_NONE && (
          <div className="flex items-center gap-2 text-xs text-emerald-600">
            <CheckCircleIcon className="size-3.5" />
            {t('configured')}
          </div>
        )}
      </div>

      <DialogFooter>
        <Button variant="outline" onClick={onCancel}>
          {tc('cancel')}
        </Button>
        <Button onClick={onSave} disabled={disabled}>
          {isPending && (
            <Loader2Icon className="size-4 animate-spin" data-icon="inline-start" />
          )}
          {tc('save')}
        </Button>
      </DialogFooter>

      <CredentialFormDialog
        open={createOpen}
        onOpenChange={setCreateOpen}
        defaultProvider={createFormDefaultProvider}
        onCreated={(c) => {
          onCredentialCreated?.(c)
        }}
      />
    </>
  )
}
