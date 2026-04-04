'use client'

import React, { useState } from 'react'
import { useTranslations } from 'next-intl'
import { KeyIcon, CheckCircleIcon, Loader2Icon } from 'lucide-react'
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
import { Input } from '@/components/ui/input'
import { useUpdateToolAuthConfig } from '@/lib/hooks/use-tools'
import type { Tool } from '@/lib/types'

interface MCPAuthDialogProps {
  tool: Tool
  trigger: React.ReactNode
}

function getAuthEntry(authConfig: Record<string, unknown> | null): {
  keyName: string
  keyValue: string
} {
  if (!authConfig) return { keyName: 'api_key', keyValue: '' }
  const entries = Object.entries(authConfig)
  if (entries.length === 0) return { keyName: 'api_key', keyValue: '' }
  const [keyName, keyValue] = entries[0]
  return { keyName, keyValue: typeof keyValue === 'string' ? keyValue : '' }
}

export function MCPAuthDialog({ tool, trigger }: MCPAuthDialogProps) {
  const t = useTranslations('tool.mcpAuth')
  const tc = useTranslations('common')
  const [open, setOpen] = useState(false)
  const updateAuth = useUpdateToolAuthConfig()

  const existing = getAuthEntry(tool.auth_config)
  const [keyName, setKeyName] = useState(existing.keyName)
  const [keyValue, setKeyValue] = useState(existing.keyValue)

  const handleSave = () => {
    updateAuth.mutate(
      { id: tool.id, authConfig: { [keyName]: keyValue } },
      { onSuccess: () => setOpen(false) },
    )
  }

  return (
    <Dialog
      open={open}
      onOpenChange={(v) => {
        setOpen(v)
        if (v) {
          const entry = getAuthEntry(tool.auth_config)
          setKeyName(entry.keyName)
          setKeyValue(entry.keyValue)
        }
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
            <label htmlFor="mcp-key-name" className="text-sm font-medium">
              {t('keyNameLabel')}
            </label>
            <Input
              id="mcp-key-name"
              placeholder="api_key"
              value={keyName}
              onChange={(e) => setKeyName(e.target.value)}
            />
          </div>
          <div className="space-y-2">
            <label htmlFor="mcp-key-value" className="text-sm font-medium">
              {t('keyValueLabel')}
            </label>
            <Input
              id="mcp-key-value"
              type="password"
              placeholder={t('keyValuePlaceholder')}
              value={keyValue}
              onChange={(e) => setKeyValue(e.target.value)}
            />
          </div>

          {existing.keyValue && (
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
      </DialogContent>
    </Dialog>
  )
}
