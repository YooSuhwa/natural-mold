'use client'

import { Handle, Position } from '@xyflow/react'
import { PlusIcon } from 'lucide-react'
import { useTranslations } from 'next-intl'
import { Button } from '@/components/ui/button'

export function SubagentsNode() {
  const t = useTranslations('agent.visualSettings')

  return (
    <>
      <Handle type="target" position={Position.Left} className="!bg-purple-500 !w-2.5 !h-2.5" />
      <div className="w-[220px] rounded-xl border bg-card shadow-md">
        <div className="flex items-center justify-between border-b px-3 py-2">
          <span className="text-[10px] font-semibold uppercase tracking-wider text-muted-foreground">
            {t('nodes.subagents')}
          </span>
          <Button variant="ghost" size="icon-sm" disabled>
            <PlusIcon className="size-3.5" />
          </Button>
        </div>
        <div className="px-3 py-2.5">
          <p className="text-xs text-muted-foreground">{t('subagents.empty')}</p>
        </div>
      </div>
    </>
  )
}
