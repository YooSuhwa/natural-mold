'use client'

import { useState } from 'react'
import { useTranslations } from 'next-intl'
import { toast } from 'sonner'
import { KeyIcon, CheckCircleIcon, Loader2Icon, LinkIcon } from 'lucide-react'

import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog'
import { Button } from '@/components/ui/button'
import { useUpdateMCPServer } from '@/lib/hooks/use-tools'
import { useCredentials } from '@/lib/hooks/use-credentials'
import { CredentialFormDialog } from '@/components/tool/credential-form-dialog'
import { CredentialSelect, CREDENTIAL_NONE } from '@/components/tool/credential-select'
import type { MCPServerListItem } from '@/lib/types'

interface MCPServerAuthDialogProps {
  server: MCPServerListItem
  open: boolean
  onOpenChange: (open: boolean) => void
}

export function MCPServerAuthDialog({ server, open, onOpenChange }: MCPServerAuthDialogProps) {
  const t = useTranslations('tool.mcpServer.auth')
  const tToast = useTranslations('tool.mcpServer.toast')
  const tc = useTranslations('common')
  const tCred = useTranslations('connections.credentialSelect')
  const updateServer = useUpdateMCPServer()
  const { data: credentials } = useCredentials()
  const [createOpen, setCreateOpen] = useState(false)
  const [mode, setMode] = useState<string>(server.credential_id ?? CREDENTIAL_NONE)

  function handleOpenChange(next: boolean) {
    if (next) setMode(server.credential_id ?? CREDENTIAL_NONE)
    onOpenChange(next)
  }

  function handleSave() {
    const credentialId = mode === CREDENTIAL_NONE ? null : mode
    updateServer.mutate(
      { id: server.id, data: { credential_id: credentialId } },
      {
        onSuccess: () => onOpenChange(false),
        onError: () => toast.error(tToast('saveFailed')),
      },
    )
  }

  const availableCredentials = credentials ?? []

  return (
    <Dialog open={open} onOpenChange={handleOpenChange}>
      <DialogContent className="sm:max-w-md">
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2">
            <KeyIcon className="size-4" />
            {t('title', { serverName: server.name })}
          </DialogTitle>
          <DialogDescription>{t('description')}</DialogDescription>
        </DialogHeader>

        <div className="space-y-4 py-2">
          <div className="space-y-2">
            <label className="text-sm font-medium flex items-center gap-1.5">
              <LinkIcon className="size-3.5" />
              {tCred('label')}
            </label>
            <CredentialSelect
              value={mode}
              onValueChange={setMode}
              onCreateRequested={() => setCreateOpen(true)}
              credentials={availableCredentials}
            />
          </div>

          {mode !== CREDENTIAL_NONE && (
            <div className="flex items-center gap-2 text-xs text-emerald-600">
              <CheckCircleIcon className="size-3.5" />
              {t('configured')}
            </div>
          )}
        </div>

        <DialogFooter>
          <Button variant="outline" onClick={() => onOpenChange(false)}>
            {tc('cancel')}
          </Button>
          <Button onClick={handleSave} disabled={updateServer.isPending}>
            {updateServer.isPending && (
              <Loader2Icon className="size-4 animate-spin" data-icon="inline-start" />
            )}
            {tc('save')}
          </Button>
        </DialogFooter>

        <CredentialFormDialog
          open={createOpen}
          onOpenChange={setCreateOpen}
          onCreated={(c) => setMode(c.id)}
        />
      </DialogContent>
    </Dialog>
  )
}
