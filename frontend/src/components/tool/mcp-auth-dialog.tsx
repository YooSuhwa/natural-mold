'use client'

import React, { useState } from 'react'
import { useTranslations } from 'next-intl'
import { KeyIcon, CheckCircleIcon, Loader2Icon, LinkIcon } from 'lucide-react'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from '@/components/ui/dialog'
import { Button } from '@/components/ui/button'
import { useUpdateToolAuthConfig } from '@/lib/hooks/use-tools'
import { useCredentials } from '@/lib/hooks/use-credentials'
import { CredentialFormDialog } from '@/components/tool/credential-form-dialog'
import { CredentialSelect, CREDENTIAL_NONE } from '@/components/tool/credential-select'
import type { Tool } from '@/lib/types'

interface MCPAuthDialogProps {
  tool: Tool
  trigger: React.ReactNode
}

export function MCPAuthDialog({ tool, trigger }: MCPAuthDialogProps) {
  const t = useTranslations('tool.mcpAuth')
  const tc = useTranslations('common')
  const tCred = useTranslations('connections.credentialSelect')
  const [open, setOpen] = useState(false)
  const [createOpen, setCreateOpen] = useState(false)
  const updateAuth = useUpdateToolAuthConfig()
  const { data: credentials } = useCredentials()

  const [mode, setMode] = useState<string>(tool.credential_id ?? CREDENTIAL_NONE)

  const availableCredentials = credentials ?? []

  const handleSave = () => {
    const credentialId = mode === CREDENTIAL_NONE ? null : mode
    updateAuth.mutate(
      { id: tool.id, authConfig: {}, credentialId },
      { onSuccess: () => setOpen(false) },
    )
  }

  return (
    <Dialog
      open={open}
      onOpenChange={(v) => {
        setOpen(v)
        if (v) setMode(tool.credential_id ?? CREDENTIAL_NONE)
      }}
    >
      <DialogTrigger render={trigger as React.ReactElement} />
      <DialogContent className="sm:max-w-md">
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2">
            <KeyIcon className="size-4" />
            {t('title', { toolName: tool.name })}
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
          <Button variant="outline" onClick={() => setOpen(false)}>
            {tc('cancel')}
          </Button>
          <Button onClick={handleSave} disabled={updateAuth.isPending}>
            {updateAuth.isPending && (
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
