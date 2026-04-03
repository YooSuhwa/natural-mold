'use client'

import { Handle, Position } from '@xyflow/react'
import { useTranslations } from 'next-intl'
import { Button } from '@/components/ui/button'

export function ChannelsNode() {
  const t = useTranslations('agent.visualSettings')

  return (
    <>
      <div className="w-[220px] rounded-xl border bg-card shadow-md">
        <div className="border-b px-3 py-2">
          <span className="text-[10px] font-semibold uppercase tracking-wider text-muted-foreground">
            {t('nodes.channels')}
          </span>
        </div>
        <div className="space-y-2 px-3 py-2.5">
          <p className="text-xs text-muted-foreground">{t('channels.count', { count: 0 })}</p>
          <p className="text-[11px] text-muted-foreground/70">{t('channels.hint')}</p>
          <Button variant="outline" size="sm" disabled className="w-full text-xs">
            {t('channels.setIdentity')}
          </Button>
        </div>
      </div>
      <Handle type="source" position={Position.Right} className="!bg-amber-500 !w-2.5 !h-2.5" />
    </>
  )
}
