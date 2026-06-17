'use client'

import { CopyIcon, KeyRoundIcon } from 'lucide-react'
import { useTranslations } from 'next-intl'
import { toast } from 'sonner'
import { Button } from '@/components/ui/button'
import { DialogShell } from '@/components/shared/dialog-shell'
import type { AgentApiKeyCreated } from '@/lib/types'

interface ApiKeyCreatedDialogProps {
  createdKey: AgentApiKeyCreated | null
  open: boolean
  onOpenChange: (open: boolean) => void
}

export function ApiKeyCreatedDialog({ createdKey, open, onOpenChange }: ApiKeyCreatedDialogProps) {
  const t = useTranslations('appSettings.agentApi.createdDialog')

  async function copyKey() {
    if (!createdKey?.key) return
    await navigator.clipboard.writeText(createdKey.key)
    toast.success(t('copied'))
  }

  return (
    <DialogShell open={open} onOpenChange={onOpenChange} size="md" height="auto">
      <DialogShell.Header
        icon={<KeyRoundIcon className="size-4" />}
        title={t('title')}
        description={t('description')}
      />
      <DialogShell.Body>
        <div className="moldy-muted-panel space-y-2 p-3">
          <p className="text-xs font-medium text-muted-foreground">{t('secretKey')}</p>
          <div className="flex items-center gap-2">
            <code className="min-w-0 flex-1 overflow-hidden text-ellipsis whitespace-nowrap text-xs">
              {createdKey?.key}
            </code>
            <Button variant="outline" size="icon-sm" onClick={copyKey} aria-label={t('copyAria')}>
              <CopyIcon className="size-4" />
            </Button>
          </div>
        </div>
      </DialogShell.Body>

      <DialogShell.Footer>
        <Button onClick={() => onOpenChange(false)}>{t('done')}</Button>
      </DialogShell.Footer>
    </DialogShell>
  )
}
