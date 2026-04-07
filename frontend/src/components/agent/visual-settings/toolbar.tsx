'use client'

import { useRouter } from 'next/navigation'
import { useTranslations } from 'next-intl'
import { ArrowLeftIcon, MessageSquareIcon, Undo2Icon, Redo2Icon } from 'lucide-react'
import { Button } from '@/components/ui/button'

import { ComingSoonButton } from '@/components/shared/coming-soon-button'

interface ToolbarProps {
  agentId?: string
  agentName: string
  onSave: () => void
  isSaving: boolean
  mode?: 'create' | 'edit'
}

export function Toolbar({ agentId, agentName, onSave, isSaving, mode = 'edit' }: ToolbarProps) {
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
            } else if (mode === 'create') {
              router.push('/agents/new')
            } else if (agentId) {
              router.push(`/agents/${agentId}`)
            }
          }}
        >
          <ArrowLeftIcon className="size-4" />
        </Button>
        <span className="text-sm font-medium truncate max-w-[200px]">{agentName}</span>
      </div>

      <div className="flex items-center gap-1">
        <ComingSoonButton title={t('toolbar.hideChat')}>
          <MessageSquareIcon className="size-4" />
        </ComingSoonButton>
        <div className="mx-1 h-4 w-px bg-border" />
        <ComingSoonButton title={t('toolbar.undo')}>
          <Undo2Icon className="size-4" />
        </ComingSoonButton>
        <ComingSoonButton title={t('toolbar.redo')}>
          <Redo2Icon className="size-4" />
        </ComingSoonButton>
        <div className="mx-1 h-4 w-px bg-border" />
        <Button size="sm" onClick={onSave} disabled={isSaving}>
          {mode === 'create' ? tc('create') : tc('save')}
        </Button>
      </div>
    </div>
  )
}
