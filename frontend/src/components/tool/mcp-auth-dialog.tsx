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

export function MCPAuthDialog({ tool, trigger }: MCPAuthDialogProps) {
  const t = useTranslations('tool.mcpAuth')
  const tc = useTranslations('common')
  const [open, setOpen] = useState(false)
  const updateAuth = useUpdateToolAuthConfig()

  const existingKey = ((tool.auth_config ?? {}) as Record<string, string>).api_key ?? ''
  const [apiKey, setApiKey] = useState(existingKey)

  const handleSave = () => {
    updateAuth.mutate(
      { id: tool.id, authConfig: { api_key: apiKey } },
      { onSuccess: () => setOpen(false) },
    )
  }

  return (
    <Dialog
      open={open}
      onOpenChange={(v) => {
        setOpen(v)
        if (v) {
          setApiKey(existingKey)
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
            <label htmlFor="mcp-api-key" className="text-sm font-medium">
              {t('apiKeyLabel')}
            </label>
            <Input
              id="mcp-api-key"
              type="password"
              placeholder={t('apiKeyPlaceholder')}
              value={apiKey}
              onChange={(e) => setApiKey(e.target.value)}
            />
          </div>

          {existingKey && (
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
