'use client'

import React, { useState } from 'react'
import { useTranslations } from 'next-intl'
import { KeyIcon, CheckCircleIcon, Loader2Icon, LinkIcon, PlusIcon } from 'lucide-react'
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
import {
  Select,
  SelectTrigger,
  SelectValue,
  SelectContent,
  SelectItem,
  SelectSeparator,
} from '@/components/ui/select'
import { useUpdateToolAuthConfig } from '@/lib/hooks/use-tools'
import { useCredentials } from '@/lib/hooks/use-credentials'
import { CredentialFormDialog } from '@/components/tool/credential-form-dialog'
import type { Tool } from '@/lib/types'

interface MCPAuthDialogProps {
  tool: Tool
  trigger: React.ReactNode
}

const NONE = 'none'
const CREATE = '__create__'

export function MCPAuthDialog({ tool, trigger }: MCPAuthDialogProps) {
  const t = useTranslations('tool.mcpAuth')
  const tc = useTranslations('common')
  const tCred = useTranslations('connections.credentialSelect')
  const [open, setOpen] = useState(false)
  const [createOpen, setCreateOpen] = useState(false)
  const updateAuth = useUpdateToolAuthConfig()
  const { data: credentials } = useCredentials()

  const [mode, setMode] = useState<string>(tool.credential_id ?? NONE)

  const availableCredentials = credentials ?? []

  function handleModeChange(v: string | null) {
    if (!v) return
    if (v === CREATE) {
      setCreateOpen(true)
      return
    }
    setMode(v)
  }

  const handleSave = () => {
    if (mode === NONE) {
      updateAuth.mutate(
        { id: tool.id, authConfig: {}, credentialId: null },
        { onSuccess: () => setOpen(false) },
      )
      return
    }
    updateAuth.mutate(
      { id: tool.id, authConfig: {}, credentialId: mode },
      { onSuccess: () => setOpen(false) },
    )
  }

  return (
    <Dialog
      open={open}
      onOpenChange={(v) => {
        setOpen(v)
        if (v) setMode(tool.credential_id ?? NONE)
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
            <Select value={mode} onValueChange={handleModeChange}>
              <SelectTrigger className="w-full">
                <SelectValue placeholder={tCred('placeholder')}>
                  {(v: string) => {
                    if (v === NONE) return tCred('none')
                    const cred = availableCredentials.find((c) => c.id === v)
                    return cred?.name ?? ''
                  }}
                </SelectValue>
              </SelectTrigger>
              <SelectContent>
                <SelectItem value={NONE}>{tCred('none')}</SelectItem>
                {availableCredentials.length > 0 && <SelectSeparator />}
                {availableCredentials.map((c) => (
                  <SelectItem key={c.id} value={c.id}>
                    {c.name}
                  </SelectItem>
                ))}
                <SelectSeparator />
                <SelectItem value={CREATE}>
                  <span className="flex items-center gap-1.5">
                    <PlusIcon className="size-3.5" />
                    {tCred('createNew')}
                  </span>
                </SelectItem>
              </SelectContent>
            </Select>
          </div>

          {mode !== NONE && (
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
