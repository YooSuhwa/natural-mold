'use client'

import { useRouter } from 'next/navigation'
import { useTranslations } from 'next-intl'
import { ArrowLeftIcon, MessageSquareIcon, Undo2Icon, Redo2Icon } from 'lucide-react'
import { Button } from '@/components/ui/button'

interface ToolbarProps {
  agentId: string
  agentName: string
  onSave: () => void
  isSaving: boolean
}

export function Toolbar({ agentId, agentName, onSave, isSaving }: ToolbarProps) {
  const router = useRouter()
  const t = useTranslations('agent.visualSettings')
  const tc = useTranslations('common')

  return (
    <div className="flex h-12 items-center justify-between border-b bg-background px-3">
      <div className="flex items-center gap-2">
        <Button
          variant="ghost"
          size="icon-sm"
          onClick={() => {
            const hasAppHistory =
              document.referrer && new URL(document.referrer).origin === window.location.origin
            if (hasAppHistory) {
              router.back()
            } else {
              router.push(`/agents/${agentId}`)
            }
          }}
        >
          <ArrowLeftIcon className="size-4" />
        </Button>
        <span className="text-sm font-medium truncate max-w-[200px]">{agentName}</span>
      </div>

      <div className="flex items-center gap-1">
        <Button variant="ghost" size="icon-sm" disabled title={t('toolbar.hideChat')}>
          <MessageSquareIcon className="size-4" />
        </Button>
        <div className="mx-1 h-4 w-px bg-border" />
        <Button variant="ghost" size="icon-sm" disabled title={t('toolbar.undo')}>
          <Undo2Icon className="size-4" />
        </Button>
        <Button variant="ghost" size="icon-sm" disabled title={t('toolbar.redo')}>
          <Redo2Icon className="size-4" />
        </Button>
        <div className="mx-1 h-4 w-px bg-border" />
        <Button size="sm" onClick={onSave} disabled={isSaving}>
          {tc('save')}
        </Button>
      </div>
    </div>
  )
}
