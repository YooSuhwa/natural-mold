'use client'

import React, { useState } from 'react'
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

const PROVIDER_DESCRIPTIONS: Record<string, string> = {
  naver: ' 네이버 개발자센터에서 발급받을 수 있습니다.',
  google_search: ' Google Cloud Console에서 API Key와 검색 엔진 ID를 발급받을 수 있습니다.',
  google_chat: ' Google Chat 스페이스 설정에서 Webhook URL을 복사하세요.',
  google_workspace: ' Google Cloud Console에서 OAuth2 인증 정보를 발급받을 수 있습니다.',
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
  const [open, setOpen] = useState(false)
  const updateAuth = useUpdateToolAuthConfig()
  const provider = detectProvider(tool.name)
  const fields = PROVIDER_FIELDS[provider] ?? []

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
            {tool.name} API 키 설정
          </DialogTitle>
          <DialogDescription>
            이 도구를 사용하려면 API 키를 설정하세요.
            {PROVIDER_DESCRIPTIONS[provider] ?? ''}
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
              API 키가 설정되어 있습니다
            </div>
          )}
        </div>

        <DialogFooter>
          <Button variant="outline" onClick={() => setOpen(false)}>
            취소
          </Button>
          <Button onClick={handleSave} disabled={updateAuth.isPending}>
            {updateAuth.isPending && (
              <Loader2Icon className="size-4 animate-spin" data-icon="inline-start" />
            )}
            저장
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
