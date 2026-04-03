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

interface PrebuiltAuthDialogProps {
  tool: Tool
  trigger: React.ReactNode
}

interface FieldDef {
  key: string
  label: string
  placeholder: string
}

const PROVIDER_FIELDS: Record<string, FieldDef[]> = {
  naver: [
    { key: 'naver_client_id', label: 'Client ID', placeholder: 'NAVER_CLIENT_ID' },
    { key: 'naver_client_secret', label: 'Client Secret', placeholder: 'NAVER_CLIENT_SECRET' },
  ],
  google_search: [
    { key: 'google_api_key', label: 'API Key', placeholder: 'GOOGLE_API_KEY' },
    { key: 'google_cse_id', label: 'Search Engine ID', placeholder: 'GOOGLE_CSE_ID' },
  ],
  google_chat: [
    {
      key: 'webhook_url',
      label: 'Webhook URL',
      placeholder: 'https://chat.googleapis.com/v1/spaces/...',
    },
  ],
  google_workspace: [
    {
      key: 'google_oauth_client_id',
      label: 'OAuth Client ID',
      placeholder: 'xxx.apps.googleusercontent.com',
    },
    { key: 'google_oauth_client_secret', label: 'OAuth Client Secret', placeholder: 'GOCSPX-xxx' },
    { key: 'google_oauth_refresh_token', label: 'Refresh Token', placeholder: '1//0xxx' },
  ],
}

function detectProvider(toolName: string): string {
  const lower = toolName.toLowerCase()
  if (lower.startsWith('naver')) return 'naver'
  if (lower.startsWith('google chat')) return 'google_chat'
  if (lower.startsWith('gmail') || lower.startsWith('calendar')) return 'google_workspace'
  if (lower.startsWith('google')) return 'google_search'
  return 'unknown'
}

export function PrebuiltAuthDialog({ tool, trigger }: PrebuiltAuthDialogProps) {
  const t = useTranslations('tool.authDialog')
  const tc = useTranslations('common')
  const [open, setOpen] = useState(false)
  const updateAuth = useUpdateToolAuthConfig()
  const provider = detectProvider(tool.name)
  const fields = PROVIDER_FIELDS[provider] ?? []
  const providerKey = (
    {
      naver: 'naver',
      google_search: 'googleSearch',
      google_chat: 'googleChat',
      google_workspace: 'googleWorkspace',
    } as Record<string, string>
  )[provider]

  const existingConfig = (tool.auth_config ?? {}) as Record<string, string>
  const [values, setValues] = useState<Record<string, string>>(() => {
    const init: Record<string, string> = {}
    for (const f of fields) {
      init[f.key] = existingConfig[f.key] ?? ''
    }
    return init
  })

  const handleSave = () => {
    const authConfig: Record<string, string> = {}
    for (const f of fields) {
      if (values[f.key]) {
        authConfig[f.key] = values[f.key]
      }
    }
    updateAuth.mutate({ id: tool.id, authConfig }, { onSuccess: () => setOpen(false) })
  }

  const hasConfig = fields.some((f) => existingConfig[f.key])

  return (
    <Dialog
      open={open}
      onOpenChange={(v) => {
        setOpen(v)
        if (v) {
          const init: Record<string, string> = {}
          for (const f of fields) init[f.key] = existingConfig[f.key] ?? ''
          setValues(init)
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
          <DialogDescription>
            {t('description')}
            {providerKey && t(`provider.${providerKey}`)}
          </DialogDescription>
        </DialogHeader>

        <div className="space-y-4 py-2">
          {fields.map((f) => (
            <div key={f.key} className="space-y-2">
              <label htmlFor={f.key} className="text-sm font-medium">
                {f.label}
              </label>
              <Input
                id={f.key}
                type="password"
                placeholder={f.placeholder}
                value={values[f.key] ?? ''}
                onChange={(e) => setValues((prev) => ({ ...prev, [f.key]: e.target.value }))}
              />
            </div>
          ))}

          {hasConfig && (
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
