'use client'

import { useMemo, useState } from 'react'
import { useTranslations } from 'next-intl'
import { toast } from 'sonner'
import { CheckCircleIcon, KeyIcon, LinkIcon, Loader2Icon } from 'lucide-react'

import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog'
import { Button } from '@/components/ui/button'
import { Skeleton } from '@/components/ui/skeleton'
import { CredentialFormDialog } from '@/components/tool/credential-form-dialog'
import { CredentialSelect, CREDENTIAL_NONE } from '@/components/tool/credential-select'
import { useCredentials } from '@/lib/hooks/use-credentials'
import {
  useConnections,
  useCreateConnection,
  useUpdateConnection,
} from '@/lib/hooks/use-connections'
import type { Connection } from '@/lib/types'

interface ConnectionBindingDialogProps {
  type: 'prebuilt'
  providerName: string
  // Dialog title에 표시할 tool name. 없으면 providerName 사용
  toolName?: string
  open: boolean
  onOpenChange: (open: boolean) => void
  // (선택) 저장 성공 시 콜백 — refresh 트리거 등
  onSaved?: (connection: Connection) => void
}

const PROVIDER_I18N_KEY: Record<string, string> = {
  naver: 'naver',
  google_search: 'googleSearch',
  google_chat: 'googleChat',
  google_workspace: 'googleWorkspace',
}

export function ConnectionBindingDialog({
  type,
  providerName,
  toolName,
  open,
  onOpenChange,
  onSaved,
}: ConnectionBindingDialogProps) {
  return (
    <Dialog
      open={open}
      onOpenChange={(v) => {
        if (!v) onOpenChange(false)
      }}
    >
      <DialogContent className="sm:max-w-md">
        {open && (
          <DialogBody
            type={type}
            providerName={providerName}
            toolName={toolName}
            onClose={() => onOpenChange(false)}
            onSaved={onSaved}
          />
        )}
      </DialogContent>
    </Dialog>
  )
}

function DialogBody({
  type,
  providerName,
  toolName,
  onClose,
  onSaved,
}: {
  type: 'prebuilt'
  providerName: string
  toolName?: string
  onClose: () => void
  onSaved?: (connection: Connection) => void
}) {
  const t = useTranslations('connections.bindingDialog')
  const tProvider = useTranslations('tool.authDialog.provider')
  const tc = useTranslations('common')
  const tCred = useTranslations('connections.credentialSelect')

  const { data: connections, isLoading: connectionsLoading } = useConnections({
    type,
    provider_name: providerName,
  })
  const { data: credentials } = useCredentials()
  const createConnection = useCreateConnection()
  const updateConnection = useUpdateConnection()
  const [createOpen, setCreateOpen] = useState(false)

  const defaultConnection = useMemo(
    () => connections?.find((c) => c.is_default) ?? null,
    [connections],
  )
  // connections 응답이 로드된 후 default connection의 credential을 선택 상태로 하이드레이트.
  // React 19 권장 패턴: render 중 setState + guard (useEffect의 cascading render 회피).
  const hydrationKey = connectionsLoading
    ? null
    : (defaultConnection?.id ?? 'empty') + ':' + (defaultConnection?.credential_id ?? 'none')
  const [mode, setMode] = useState<string>(CREDENTIAL_NONE)
  const [hydratedFor, setHydratedFor] = useState<string | null>(null)
  if (hydrationKey !== null && hydrationKey !== hydratedFor) {
    setHydratedFor(hydrationKey)
    setMode(defaultConnection?.credential_id ?? CREDENTIAL_NONE)
  }

  const matchingCredentials = useMemo(
    () => credentials?.filter((c) => c.provider_name === providerName) ?? [],
    [credentials, providerName],
  )

  const providerI18nKey = PROVIDER_I18N_KEY[providerName]
  const displayTitle = toolName ?? providerName
  const isPending = createConnection.isPending || updateConnection.isPending

  async function handleSave() {
    const credentialId = mode === CREDENTIAL_NONE ? null : mode
    try {
      let result: Connection
      if (defaultConnection) {
        result = await updateConnection.mutateAsync({
          id: defaultConnection.id,
          data: { credential_id: credentialId },
        })
      } else {
        if (credentialId === null) {
          // default connection이 없고 credential도 선택하지 않음 → 아무것도 생성 안 함
          onClose()
          return
        }
        result = await createConnection.mutateAsync({
          type,
          provider_name: providerName,
          display_name: toolName ?? providerName,
          credential_id: credentialId,
          is_default: true,
        })
      }
      toast.success(t('toast.saved'))
      onSaved?.(result)
      onClose()
    } catch {
      toast.error(t('toast.saveFailed'))
    }
  }

  return (
    <>
      <DialogHeader>
        <DialogTitle className="flex items-center gap-2">
          <KeyIcon className="size-4" />
          {t('title', { name: displayTitle })}
        </DialogTitle>
        <DialogDescription>
          {t('description')}
          {providerI18nKey && tProvider(providerI18nKey)}
        </DialogDescription>
      </DialogHeader>

      <div className="space-y-4 py-2">
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
              onValueChange={setMode}
              onCreateRequested={() => setCreateOpen(true)}
              credentials={matchingCredentials}
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
        <Button variant="outline" onClick={onClose}>
          {tc('cancel')}
        </Button>
        <Button onClick={handleSave} disabled={isPending}>
          {isPending && (
            <Loader2Icon className="size-4 animate-spin" data-icon="inline-start" />
          )}
          {tc('save')}
        </Button>
      </DialogFooter>

      <CredentialFormDialog
        open={createOpen}
        onOpenChange={setCreateOpen}
        defaultProvider={providerName}
        onCreated={(c) => {
          if (c.provider_name === providerName) setMode(c.id)
        }}
      />
    </>
  )
}
