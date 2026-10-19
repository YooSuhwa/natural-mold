'use client'

import { useTranslations } from 'next-intl'
import { Loader2Icon, PlugZapIcon } from 'lucide-react'

import { DialogShell } from '@/components/shared/dialog-shell'
import { Button } from '@/components/ui/button'
import { useAgentReadiness } from '@/lib/hooks/use-readiness'

import { canStart } from './readiness-actions'
import { ReadinessPanel } from './readiness-panel'

interface ReadinessDialogProps {
  readonly open: boolean
  readonly onOpenChange: (open: boolean) => void
  readonly agentId: string
  readonly agentName?: string | null
  readonly onConnected?: () => void
}

/** S21 "대화 시작 전 점검" (plan 13.5): opened from the chat screen and a 409. */
export function ReadinessDialog({
  open,
  onOpenChange,
  agentId,
  agentName,
  onConnected,
}: ReadinessDialogProps) {
  const t = useTranslations('readiness.panel')
  const readiness = useAgentReadiness(agentId, open)
  const data = readiness.data

  return (
    <DialogShell open={open} onOpenChange={onOpenChange} size="md" height="auto">
      <DialogShell.Header
        icon={<PlugZapIcon className="size-5" />}
        title={agentName ? t('title', { name: agentName }) : t('titleUnnamed')}
      />
      <DialogShell.Body>
        {data ? (
          <ReadinessPanel
            agentId={agentId}
            agentName={agentName}
            readiness={data}
            onConnected={onConnected}
          />
        ) : readiness.isError ? (
          <div role="alert" className="space-y-2 text-sm">
            <p className="text-destructive">{t('loadFailed')}</p>
            <Button size="sm" variant="outline" onClick={() => void readiness.refetch()}>
              {t('retry')}
            </Button>
          </div>
        ) : (
          <p className="flex items-center gap-2 text-sm text-muted-foreground" role="status">
            <Loader2Icon className="size-4 animate-spin" aria-hidden />
            {t('loading')}
          </p>
        )}
      </DialogShell.Body>
      <DialogShell.Footer>
        <Button variant="outline" onClick={() => onOpenChange(false)}>
          {t('close')}
        </Button>
        <Button disabled={!data || !canStart(data.overall)} onClick={() => onOpenChange(false)}>
          {t('confirm')}
        </Button>
      </DialogShell.Footer>
    </DialogShell>
  )
}
