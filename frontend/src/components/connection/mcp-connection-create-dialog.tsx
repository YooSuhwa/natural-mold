'use client'

import { useState } from 'react'
import { ServerIcon, Loader2Icon } from 'lucide-react'
import { toast } from 'sonner'
import { useTranslations } from 'next-intl'

import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { ApiError } from '@/lib/api/client'
import {
  useCreateConnection,
  useDiscoverMcpTools,
} from '@/lib/hooks/use-connections'

interface McpConnectionCreateDialogProps {
  open: boolean
  onOpenChange: (open: boolean) => void
}

// 표시 이름 → provider_name 슬러그. 백엔드 validator는 ^[a-z0-9_]+$ 강제 (길이 ≤50).
// 빈 결과는 `mcp`로 fallback, 백엔드에서 display_name 중복은 허용 (PK로 구분).
function slugify(raw: string): string {
  const normalized = raw
    .trim()
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, '_')
    .replace(/^_+|_+$/g, '')
    .slice(0, 50)
  return normalized || 'mcp'
}

/**
 * MCP 서버 신규 등록 다이얼로그 (M6.1 M7).
 *
 * v1 스코프: URL + display_name 만 받아 `auth_type='none'`으로 connection 생성 +
 * tool discovery 자동 실행. 인증이 필요한 서버는 생성 후 Connection Detail Sheet
 * 에서 credential 연결 (기존 UI 재사용).
 */
export function McpConnectionCreateDialog({
  open,
  onOpenChange,
}: McpConnectionCreateDialogProps) {
  const t = useTranslations('connections.mcpCreateDialog')
  const tc = useTranslations('common')
  const [displayName, setDisplayName] = useState('')
  const [url, setUrl] = useState('')
  const createConnection = useCreateConnection()
  const discover = useDiscoverMcpTools()

  function reset() {
    setDisplayName('')
    setUrl('')
  }

  function handleClose() {
    reset()
    onOpenChange(false)
  }

  async function handleSubmit() {
    const trimmedName = displayName.trim()
    const trimmedUrl = url.trim()
    if (!trimmedName || !trimmedUrl) return

    let connectionId: string
    try {
      const created = await createConnection.mutateAsync({
        type: 'mcp',
        provider_name: slugify(trimmedName),
        display_name: trimmedName,
        extra_config: { url: trimmedUrl, auth_type: 'none' },
      })
      connectionId = created.id
    } catch (err) {
      if (err instanceof ApiError) {
        toast.error(t('connectionFailed', { detail: err.message }))
      } else {
        toast.error(t('connectionFailed', { detail: String(err) }))
      }
      return
    }

    try {
      const result = await discover.mutateAsync(connectionId)
      const created = result.items.filter((i) => i.status === 'created').length
      const existing = result.items.filter((i) => i.status === 'existing').length
      toast.success(t('discoverSuccess', { created, existing }))
    } catch (err) {
      if (err instanceof ApiError) {
        toast.error(t('discoverFailed', { detail: err.message }))
      } else {
        toast.error(t('discoverFailed', { detail: String(err) }))
      }
      // connection은 이미 생성됨 — 사용자가 detail에서 재시도 가능
    }

    reset()
    onOpenChange(false)
  }

  const isPending = createConnection.isPending || discover.isPending

  return (
    <Dialog
      open={open}
      onOpenChange={(v) => {
        if (!v) handleClose()
        else onOpenChange(true)
      }}
    >
      <DialogContent className="sm:max-w-md">
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2">
            <ServerIcon className="size-4" />
            {t('title')}
          </DialogTitle>
          <DialogDescription>{t('description')}</DialogDescription>
        </DialogHeader>

        <div className="space-y-4 py-2">
          <div className="space-y-2">
            <label className="text-sm font-medium">
              {t('displayName')} <span className="text-destructive">{tc('required')}</span>
            </label>
            <Input
              value={displayName}
              onChange={(e) => setDisplayName(e.target.value)}
              placeholder={t('displayNamePlaceholder')}
              maxLength={200}
            />
          </div>
          <div className="space-y-2">
            <label className="text-sm font-medium">
              {t('url')} <span className="text-destructive">{tc('required')}</span>
            </label>
            <Input
              value={url}
              onChange={(e) => setUrl(e.target.value)}
              placeholder="https://example.com/mcp"
              type="url"
              maxLength={500}
            />
          </div>
          <p className="text-xs text-muted-foreground">{t('authNoticeV1')}</p>
        </div>

        <DialogFooter>
          <Button variant="outline" onClick={handleClose}>
            {tc('cancel')}
          </Button>
          <Button
            onClick={handleSubmit}
            disabled={!displayName.trim() || !url.trim() || isPending}
          >
            {isPending && <Loader2Icon className="size-4 animate-spin" data-icon="inline-start" />}
            {t('submit')}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
